# CUI // SP-CTI
"""Genesis Reflex — NOCC Alarm Triage (2h cadence).

Fetches active, unacknowledged alarms from noc_alarms, correlates alarm
storms using adaptive anomaly detection (z-score on per-device alarm history),
and auto-creates P2 incidents for correlated storms that have no existing
open incident.

Detection hierarchy:
  1. Statistical (z-score) — if ≥10 historical windows exist for device
  2. LLM contextual — borderline cases where count > static floor but < dynamic
  3. Static fallback — insufficient history or upstream failures

Air-gap safe: LLM calls are optional and degrade gracefully to statistical/static.
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import json as _json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = get_logger(__name__)

CADENCE_HOURS = 2

# Observation window (how far back the active alarm query looks)
_STORM_WINDOW_MINUTES = 15

# Anomaly detection configuration
_ANOMALY_SIGMA = 2.0           # Z-score threshold for statistical anomaly
_ANOMALY_MIN_HISTORY = 10      # Minimum historical windows needed for z-score method
_ANOMALY_HISTORY_DAYS = 7      # Days of history to bucket for baseline
_ANOMALY_MODEL = "claude-haiku-4-5-20251001"
_BORDERLINE_RATIO = 0.7        # LLM only invoked when count >= this fraction of dynamic threshold

# Static fallback threshold (used when insufficient history)
_FALLBACK_STORM_MIN_ALARMS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


# ---------------------------------------------------------------------------
# Anomaly detection helpers
# ---------------------------------------------------------------------------

def _fetch_device_alarm_history(conn, device: str, window_minutes: int, history_days: int) -> List[int]:
    """Return per-window alarm counts for a device over the last *history_days* days.

    Groups raw alarm rows into *window_minutes*-wide buckets in Python (avoids
    PG vs SQLite time-bucketing divergence).  Excludes the current window so
    the baseline is not contaminated by the storm being evaluated.
    """
    current_window_start = (_utcnow() - timedelta(minutes=window_minutes)).isoformat()
    history_cutoff = (_utcnow() - timedelta(days=history_days)).isoformat()
    try:
        rows = _try_exec(
            conn,
            "SELECT first_seen FROM noc_alarms WHERE device_name = %s "
            "AND first_seen >= %s AND first_seen < %s",
            "SELECT first_seen FROM noc_alarms WHERE device_name = ? "
            "AND first_seen >= ? AND first_seen < ?",
            (device, history_cutoff, current_window_start),
        ).fetchall()
    except Exception:
        return []

    bucket_secs = window_minutes * 60
    buckets: Dict[int, int] = {}
    for row in rows:
        ts_raw = row[0] if not hasattr(row, "keys") else row["first_seen"]
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue
            bucket = int(ts.timestamp()) // bucket_secs
            buckets[bucket] = buckets.get(bucket, 0) + 1
        except Exception:
            continue
    return list(buckets.values())


def _compute_dynamic_threshold(
    history_counts: List[int],
    fallback: int = _FALLBACK_STORM_MIN_ALARMS,
) -> Tuple[int, str]:
    """Derive an adaptive storm threshold via z-score on historical window counts.

    Returns ``(threshold, method)`` where *method* is one of:
    - ``'statistical'``        — mean + sigma*std with adequate history
    - ``'statistical_stable'`` — device is stable (low std); add fixed buffer
    - ``'static'``             — insufficient history; use *fallback*
    """
    if len(history_counts) < _ANOMALY_MIN_HISTORY:
        return fallback, "static"

    mean = sum(history_counts) / len(history_counts)
    variance = sum((x - mean) ** 2 for x in history_counts) / len(history_counts)
    std = variance ** 0.5

    if std < 0.5:
        # Stable device: threshold = mean + fixed buffer (avoids extremely low thresholds)
        threshold = max(int(mean) + fallback, fallback)
        return threshold, "statistical_stable"

    threshold = max(int(mean + _ANOMALY_SIGMA * std) + 1, 2)
    return threshold, "statistical"


def _llm_assess_storm(
    alarms: List[Any],
    device: str,
    alarm_count: int,
    threshold: int,
) -> Optional[bool]:
    """Ask the LLM whether a borderline alarm cluster is a genuine storm.

    Returns ``True`` (storm), ``False`` (not a storm), or ``None`` on error.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        summaries = []
        for a in alarms[:10]:
            if hasattr(a, "keys"):
                summaries.append({
                    "severity": a.get("severity", "unknown"),
                    "alarm_type": a.get("alarm_type", "unknown"),
                    "description": str(a.get("description", ""))[:120],
                })
            else:
                summaries.append({
                    "severity": a[4] if len(a) > 4 else "unknown",
                    "alarm_type": a[5] if len(a) > 5 else "unknown",
                    "description": str(a[6])[:120] if len(a) > 6 else "",
                })

        prompt = (
            f"Device '{device}' has {alarm_count} active alarms in the last "
            f"{_STORM_WINDOW_MINUTES} minutes (adaptive storm threshold: {threshold}).\n"
            f"Alarm sample (up to 10):\n{_json.dumps(summaries, indent=2)}\n\n"
            "Is this an anomalous alarm storm that warrants incident creation?\n"
            "Consider: diverse types/severities = likely real issue; repetitive same-type = likely flap/noise.\n"
            'Respond ONLY with JSON: {"is_storm": true|false, "rationale": "<one sentence>"}'
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a NOC anomaly detection assistant. Assess alarm clusters for genuine "
                "network issues. Be conservative — only flag true anomalies to avoid alert fatigue."
            ),
            model=_ANOMALY_MODEL,
            max_tokens=128,
            temperature=0.0,
            agent_id="nocc-alarm-anomaly-detector",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )

        router = LLMRouter()
        response = router.invoke("anomaly_detection", request)
        parsed = _json.loads(response.content.strip())
        logger.debug(
            "LLM storm assessment: device=%s count=%d threshold=%d result=%s rationale=%s",
            device, alarm_count, threshold,
            parsed.get("is_storm"), parsed.get("rationale", ""),
        )
        return bool(parsed.get("is_storm", True))
    except Exception as exc:
        logger.debug("LLM storm assessment failed (%s), using static fallback", exc)
        return None


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Triage active NOCC alarms; auto-create incidents for anomalous storms.

    Returns:
        active_alarms: int
        storms_detected: int
        incidents_created: int
        events_published: int
        detection_methods: dict[str, int]  — counts by method used
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "active_alarms": 0,
        "storms_detected": 0,
        "incidents_created": 0,
        "events_published": 0,
        "detection_methods": {"static": 0, "statistical": 0, "statistical_stable": 0, "llm": 0},
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.noc_canvas.db.init_db import get_connection as nocc_conn
        db = nocc_conn()
        try:
            _run_triage(db, dry_run, result)
        finally:
            db.close()
    except Exception as exc:
        logger.error("nocc_alarm_triage reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _run_triage(conn, dry_run: bool, result: Dict[str, Any]) -> None:
    cutoff = (_utcnow() - timedelta(minutes=_STORM_WINDOW_MINUTES)).isoformat()

    # Fetch recent active, unacknowledged alarms
    try:
        rows = _try_exec(
            conn,
            "SELECT id, device_name, circuit_id, carrier, severity, alarm_type, description, first_seen "
            "FROM noc_alarms WHERE cleared = FALSE AND acknowledged = FALSE AND first_seen >= %s",
            "SELECT id, device_name, circuit_id, carrier, severity, alarm_type, description, first_seen "
            "FROM noc_alarms WHERE cleared = 0 AND acknowledged = 0 AND first_seen >= ?",
            (cutoff,),
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"alarm_fetch: {exc}")
        return

    result["active_alarms"] = len(rows)

    # Group by device_name
    by_device: Dict[str, List[Any]] = {}
    for row in rows:
        device = (row["device_name"] if hasattr(row, "keys") else row[1]) or ""
        by_device.setdefault(device, []).append(row)

    # Detect storms using adaptive anomaly detection
    storms = []
    for device, alarms in by_device.items():
        alarm_count = len(alarms)

        history = _fetch_device_alarm_history(conn, device, _STORM_WINDOW_MINUTES, _ANOMALY_HISTORY_DAYS)
        dynamic_threshold, method = _compute_dynamic_threshold(history)

        borderline_floor = int(dynamic_threshold * _BORDERLINE_RATIO)
        if alarm_count >= dynamic_threshold:
            is_storm = True
            used_method = method
        elif alarm_count >= _FALLBACK_STORM_MIN_ALARMS and alarm_count >= borderline_floor:
            # Borderline zone: above static floor AND within 30% below dynamic threshold — ask LLM
            llm_result = _llm_assess_storm(alarms, device, alarm_count, dynamic_threshold)
            if llm_result is not None:
                is_storm = llm_result
                used_method = "llm"
            else:
                # LLM unavailable: conservatively flag as storm
                is_storm = True
                used_method = "static"
        else:
            is_storm = False
            used_method = method

        if is_storm:
            severities = [
                (a["severity"] if hasattr(a, "keys") else a[4]) for a in alarms
            ]
            worst = _worst_severity(severities)
            storms.append({
                "device": device,
                "alarm_count": alarm_count,
                "worst_severity": worst,
                "alarms": alarms,
                "threshold": dynamic_threshold,
                "detection_method": used_method,
            })
            dm_key = used_method if used_method in result["detection_methods"] else "static"
            result["detection_methods"][dm_key] += 1

    result["storms_detected"] = len(storms)

    for storm in storms:
        if dry_run:
            result["incidents_created"] += 1
            continue

        device = storm["device"]
        # Check for existing open incident on this device
        try:
            existing = _try_exec(
                conn,
                "SELECT id FROM noc_incidents WHERE affected_circuit LIKE %s "
                "AND status NOT IN ('resolved','closed') LIMIT 1",
                "SELECT id FROM noc_incidents WHERE affected_circuit LIKE ? "
                "AND status NOT IN ('resolved','closed') LIMIT 1",
                (f"%{device}%",),
            ).fetchone()
        except Exception:
            existing = None

        if existing:
            continue

        # Auto-create incident
        sev = "p2" if storm["worst_severity"] in ("critical", "major") else "p3"
        title = (
            f"Alarm storm: {storm['alarm_count']} alarms on {device} "
            f"in {_STORM_WINDOW_MINUTES}min [{storm['detection_method']}]"
        )
        try:
            _try_exec(
                conn,
                "INSERT INTO noc_incidents (incident_number, title, severity, status, affected_circuit, "
                "opened_by, assigned_to, classification) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                "INSERT INTO noc_incidents (incident_number, title, severity, status, affected_circuit, "
                "opened_by, assigned_to, classification) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"INC-AUTO-{device[:8].upper()}-{_utcnow().strftime('%Y%m%d%H%M')}",
                    title, sev, "open", device, "genesis-reflex", "noc-team", "CUI // SP-CTI",
                ),
            )
            try:
                conn.commit()
            except Exception:
                pass
            result["incidents_created"] += 1

            try:
                from tools.canvas.event_bus import publish
                publish("nocc", "nocc.incident.auto_created", {
                    "device": device,
                    "alarm_count": storm["alarm_count"],
                    "severity": sev,
                    "title": title,
                    "detection_method": storm["detection_method"],
                    "threshold": storm["threshold"],
                })
                result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

        except Exception as exc:
            result["errors"].append(f"incident_create({device}): {exc}")


def _worst_severity(severities: List[str]) -> str:
    order = ["critical", "major", "minor", "warning", "info"]
    for s in order:
        if s in severities:
            return s
    return "info"


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    print(_json.dumps(run({"dry_run": True}), indent=2))
