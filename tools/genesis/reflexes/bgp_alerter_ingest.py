# CUI // SP-CTI
"""Genesis Reflex — BGPalerter Alert Ingest (1h cadence).

BGPalerter (https://github.com/nttgin/BGPalerter) monitors BGP state changes
and writes JSON alert records. This reflex reads BGPalerter output and creates
NOCC alarms for:
  - Prefix hijacking (origin change, sub-prefix announcement)
  - BGP session state changes
  - RPKI validity changes
  - Route leak indicators

Anomaly detection: optional LLM-based analysis (BGPALERTER_LLM_ANOMALY=true).
Air-gap safe: LLM path is gated and degrades to static burst detection silently.

BGPalerter setup: enable the "reportFile" output plugin and point it to
BGPALERTER_ALERT_DIR. Each alert is one NDJSON line per file.

Configuration (env):
  BGPALERTER_ALERT_DIR        — path to BGPalerter JSON alert output directory
  BGPALERTER_MAX_AGE_H        — only ingest alerts younger than N hours (default: 2)
  BGPALERTER_LLM_ANOMALY      — enable LLM anomaly detection (default: false)
  BGPALERTER_LLM_ANOMALY_MODEL — model override (default: claude-haiku-4-5-20251001)
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from tools.logging.icdev_logger import get_logger

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 1
BGPALERTER_ALERT_DIR = os.environ.get("BGPALERTER_ALERT_DIR", "")
BGPALERTER_MAX_AGE_H = int(os.environ.get("BGPALERTER_MAX_AGE_H", "2"))
BGPALERTER_BURST_THRESHOLD = int(os.environ.get("BGPALERTER_BURST_THRESHOLD", "5"))
BGPALERTER_DESC_MAX_LEN = int(os.environ.get("BGPALERTER_DESC_MAX_LEN", "500"))
BGPALERTER_LLM_ANOMALY = os.environ.get("BGPALERTER_LLM_ANOMALY", "false").lower() == "true"
BGPALERTER_LLM_ANOMALY_MODEL = os.environ.get(
    "BGPALERTER_LLM_ANOMALY_MODEL", "claude-haiku-4-5-20251001"
)

# Maps BGPalerter alert type → (nocc_severity, nocc_alarm_type)
_TYPE_MAP: dict[str, tuple[str, str]] = {
    "hijack":        ("critical", "bgp"),
    "newPrefix":     ("major",    "bgp"),
    "subPrefix":     ("major",    "bgp"),
    "visibility":    ("major",    "bgp"),
    "rpki":          ("major",    "bgp"),
    "routedChanges": ("minor",    "bgp"),
    "peerNack":      ("major",    "bgp"),
    "session":       ("critical", "bgp"),
    "error":         ("major",    "bgp"),
}


_SEVERITY_ESCALATE: dict[str, str] = {
    "minor": "major",
    "major": "critical",
    "critical": "critical",
}

_ANOMALY_SYSTEM_PROMPT = (
    "You are a BGP network anomaly detection system. "
    "Analyze the provided alert statistics and identify anomalous burst patterns. "
    "Apply these rules: hijack/session (base=critical) escalate only if count>=10; "
    "newPrefix/subPrefix/visibility/rpki/peerNack/error (base=major) escalate to critical "
    "if count>=burst_threshold; routedChanges (base=minor) escalate to major if count>=burst_threshold. "
    "Return ONLY valid JSON: "
    '{\"anomalies\": [{\"alert_type\": \"<type>\", \"severity\": \"<critical|major|minor>\", '
    '\"reason\": \"<brief>\"}]}. '
    "If no anomalies, return: {\"anomalies\": []}"
)


def _detect_anomalies_llm(
    alerts: list[dict], burst_threshold: int
) -> "dict[str, str] | None":
    """LLM-based anomaly detection. Returns None on any failure (caller uses static fallback)."""
    if not BGPALERTER_LLM_ANOMALY:
        return None
    try:
        from tools.llm.router import LLMRouter  # type: ignore
        from tools.llm.provider import LLMRequest  # type: ignore

        counts: dict[str, int] = {}
        for alert in alerts:
            t = alert.get("type", "error")
            counts[t] = counts.get(t, 0) + 1

        user_content = json.dumps(
            {
                "alert_counts": counts,
                "burst_threshold": burst_threshold,
                "total_alerts": len(alerts),
                "type_severity_map": {k: v[0] for k, v in _TYPE_MAP.items()},
            },
            indent=2,
        )
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_ANOMALY_SYSTEM_PROMPT,
            model=BGPALERTER_LLM_ANOMALY_MODEL,
            max_tokens=256,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        response = LLMRouter().invoke("bgp_anomaly_detection", request)
        if not (response and response.content):
            return None
        data = json.loads(response.content.strip())
        return {
            item["alert_type"]: item["severity"]
            for item in data.get("anomalies", [])
            if "alert_type" in item and "severity" in item
        }
    except Exception as exc:
        logger.debug("BGP LLM anomaly detection failed, using static fallback: %s", exc)
        return None


def _truncate_desc(text: str, max_len: int) -> str:
    return text[:max_len]


def _detect_anomalies(alerts: list[dict], burst_threshold: int) -> dict[str, str]:
    """Return {alert_type: escalated_severity} for types whose count >= burst_threshold.

    Burst = more alerts of the same type than expected in one cadence window.
    Severity is escalated one level above the _TYPE_MAP baseline.
    """
    counts: dict[str, int] = {}
    for alert in alerts:
        t = alert.get("type", "error")
        counts[t] = counts.get(t, 0) + 1

    result: dict[str, str] = {}
    for alert_type, count in counts.items():
        if count >= burst_threshold:
            base_severity, _ = _TYPE_MAP.get(alert_type, ("minor", "bgp"))
            result[alert_type] = _SEVERITY_ESCALATE.get(base_severity, "major")
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_exec(conn, sql_pg: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
    except Exception:
        conn.execute(sql_pg.replace("%s", "?"), params)


def _already_open(conn, device_name: str, alarm_type: str) -> bool:
    """Return True if an identical uncleared alarm already exists (dedup guard)."""
    try:
        row = conn.execute(
            """SELECT id FROM noc_alarms WHERE device_name=%s
               AND alarm_type=%s AND cleared=0 LIMIT 1""",
            (device_name, alarm_type),
        ).fetchone()
    except Exception:
        try:
            row = conn.execute(
                """SELECT id FROM noc_alarms WHERE device_name=?
                   AND alarm_type=? AND cleared=0 LIMIT 1""",
                (device_name, alarm_type),
            ).fetchone()
        except Exception:
            return False
    return row is not None


def _insert_alarm(conn, *, severity: str, alarm_type: str,
                  device_name: str, description: str) -> bool:
    if _already_open(conn, device_name, alarm_type):
        return False
    now = _now()
    _safe_exec(
        conn,
        """INSERT INTO noc_alarms
           (alarm_source, severity, alarm_type, device_name, circuit_id,
            carrier, description, suppressed, acknowledged, cleared,
            first_seen, last_seen, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        ("bgpalerter", severity, alarm_type, device_name, "", "",
         description[:BGPALERTER_DESC_MAX_LEN], 0, 0, 0, now, now, "CUI"),
    )
    return True


def _read_alerts(alert_dir: str, max_age_hours: int) -> list[dict]:
    """Read BGPalerter NDJSON files newer than max_age_hours."""
    records: list[dict] = []
    base = Path(alert_dir)
    if not base.exists():
        return records
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    for jf in sorted(base.glob("*.json")):
        try:
            mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue
            with open(jf, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            pass
    return records


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Ingest BGPalerter alerts into NOCC alarms table.

    Returns:
        alarms_created:    int — new NOCC alarms created
        alerts_processed:  int — total alert records read
        errors:            list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "alarms_created": 0,
        "alerts_processed": 0,
        "anomalies_detected": 0,
        "anomaly_method": "static",
        "errors": [],
        "status": "ok",
    }

    if not BGPALERTER_ALERT_DIR:
        result["status"] = "skipped"
        result["reason"] = "BGPALERTER_ALERT_DIR not configured"
        return result

    burst_threshold = ctx.get("burst_threshold", BGPALERTER_BURST_THRESHOLD)
    desc_max_len = ctx.get("desc_max_len", BGPALERTER_DESC_MAX_LEN)

    alerts = _read_alerts(BGPALERTER_ALERT_DIR, BGPALERTER_MAX_AGE_H)
    if not alerts:
        result["reason"] = (
            f"No alerts in {BGPALERTER_ALERT_DIR} "
            f"within last {BGPALERTER_MAX_AGE_H}h"
        )
        return result

    llm_anomaly_map = _detect_anomalies_llm(alerts, burst_threshold)
    if llm_anomaly_map is not None:
        anomaly_map = llm_anomaly_map
        result["anomaly_method"] = "llm"
    else:
        anomaly_map = _detect_anomalies(alerts, burst_threshold)
    result["anomalies_detected"] = len(anomaly_map)

    _conn = conn
    _owned = False
    if _conn is None:
        try:
            from tools.noc_canvas.db.init_db import get_connection as _nocc_conn
            _conn = _nocc_conn()
            _owned = True
        except Exception as exc:
            result["status"] = "error"
            result["errors"].append(f"Cannot connect to NOCC DB: {exc}")
            return result

    try:
        for alert in alerts:
            result["alerts_processed"] += 1
            try:
                alert_type = alert.get("type", "error")
                base_severity, alarm_type = _TYPE_MAP.get(alert_type, ("minor", "bgp"))
                # Anomaly detection: burst escalates severity beyond static baseline
                severity = anomaly_map.get(alert_type, base_severity)
                prefix = alert.get("prefix", "")
                description = _truncate_desc(
                    alert.get("message", str(alert)), desc_max_len
                )
                device_name = prefix or alert.get("peer", "bgp_monitor")

                if not dry_run:
                    created = _insert_alarm(
                        _conn,
                        severity=severity,
                        alarm_type=alarm_type,
                        device_name=device_name,
                        description=description,
                    )
                    if created:
                        result["alarms_created"] += 1
            except Exception as exc:
                result["errors"].append(str(exc))

        if not dry_run and result["alarms_created"] > 0:
            _conn.commit()

    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        if _owned and _conn:
            try:
                _conn.close()
            except Exception:
                pass

    return result
