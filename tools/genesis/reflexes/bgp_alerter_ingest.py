# CUI // SP-CTI
"""Genesis Reflex — BGPalerter Alert Ingest (1h cadence).

BGPalerter (https://github.com/nttgin/BGPalerter) monitors BGP state changes
and writes JSON alert records. This reflex reads BGPalerter output and creates
NOCC alarms for:
  - Prefix hijacking (origin change, sub-prefix announcement)
  - BGP session state changes
  - RPKI validity changes
  - Route leak indicators

Air-gap safe: no LLM calls — pure file/DB I/O.

BGPalerter setup: enable the "reportFile" output plugin and point it to
BGPALERTER_ALERT_DIR. Each alert is one NDJSON line per file.

Configuration (env):
  BGPALERTER_ALERT_DIR  — path to BGPalerter JSON alert output directory
  BGPALERTER_MAX_AGE_H  — only ingest alerts younger than N hours (default: 2)
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
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
         description[:500], 0, 0, 0, now, now, "CUI"),
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
        "errors": [],
        "status": "ok",
    }

    if not BGPALERTER_ALERT_DIR:
        result["status"] = "skipped"
        result["reason"] = "BGPALERTER_ALERT_DIR not configured"
        return result

    alerts = _read_alerts(BGPALERTER_ALERT_DIR, BGPALERTER_MAX_AGE_H)
    if not alerts:
        result["reason"] = (
            f"No alerts in {BGPALERTER_ALERT_DIR} "
            f"within last {BGPALERTER_MAX_AGE_H}h"
        )
        return result

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
                severity, alarm_type = _TYPE_MAP.get(alert_type, ("minor", "bgp"))
                prefix = alert.get("prefix", "")
                description = alert.get("message", str(alert))[:500]
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
