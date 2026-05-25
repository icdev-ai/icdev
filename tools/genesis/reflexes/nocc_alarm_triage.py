# CUI // SP-CTI
"""Genesis Reflex — NOCC Alarm Triage (2h cadence).

Fetches active, unacknowledged alarms from noc_alarms, correlates alarm
storms (≥5 alarms from the same device within a 15-minute window), and
auto-creates P2 incidents for correlated storms that have no existing
open incident.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = get_logger(__name__)

CADENCE_HOURS = 2

# Storm threshold: ≥5 alarms from one device within 15 minutes
_STORM_MIN_ALARMS = 5
_STORM_WINDOW_MINUTES = 15


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Triage active NOCC alarms; auto-create incidents for alarm storms.

    Returns:
        active_alarms: int
        storms_detected: int
        incidents_created: int
        events_published: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "active_alarms": 0,
        "storms_detected": 0,
        "incidents_created": 0,
        "events_published": 0,
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
        if hasattr(row, "keys"):
            device = row["device_name"] or ""
        else:
            device = row[1] or ""
        by_device.setdefault(device, []).append(row)

    # Detect storms
    storms = []
    for device, alarms in by_device.items():
        if len(alarms) >= _STORM_MIN_ALARMS:
            severities = []
            for a in alarms:
                s = a["severity"] if hasattr(a, "keys") else a[4]
                severities.append(s)
            worst = _worst_severity(severities)
            storms.append({"device": device, "alarm_count": len(alarms), "worst_severity": worst, "alarms": alarms})

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
                "SELECT id FROM noc_incidents WHERE affected_circuit LIKE %s AND status NOT IN ('resolved','closed') LIMIT 1",
                "SELECT id FROM noc_incidents WHERE affected_circuit LIKE ? AND status NOT IN ('resolved','closed') LIMIT 1",
                (f"%{device}%",),
            ).fetchone()
        except Exception:
            existing = None

        if existing:
            continue

        # Auto-create incident
        sev = "p2" if storm["worst_severity"] in ("critical", "major") else "p3"
        title = f"Alarm storm: {storm['alarm_count']} alarms on {device} in {_STORM_WINDOW_MINUTES}min"
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
    import json as _json
    print(_json.dumps(run({"dry_run": True}), indent=2))