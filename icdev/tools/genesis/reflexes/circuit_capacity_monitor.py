# CUI // SP-CTI
"""Genesis Reflex — CCC Circuit Capacity Monitor (4h cadence).

Monitors all active CCC circuits; for any circuit ≥70% utilization
publishes a canvas warning event and updates noc_alarms if NOCC is enabled.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict, List

logger = get_logger(__name__)

CADENCE_HOURS = 4
WARN_THRESHOLD_PCT = 70.0
CRITICAL_THRESHOLD_PCT = 85.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Scan CCC circuits for high utilization and publish events.

    Returns:
        circuits_checked: int
        warn_circuits: list of {circuit_id, carrier, utilization_pct}
        critical_circuits: list of {circuit_id, carrier, utilization_pct}
        events_published: int
        alarms_created: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "circuits_checked": 0,
        "warn_circuits": [],
        "critical_circuits": [],
        "events_published": 0,
        "alarms_created": 0,
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.ccc_canvas.db.init_db import get_connection as ccc_conn
        conn_ccc = ccc_conn()
        try:
            rows = conn_ccc.execute(
                "SELECT circuit_id, carrier, bandwidth_gbps, utilization_pct, status "
                "FROM ccc_circuits WHERE status='active' ORDER BY utilization_pct DESC"
            ).fetchall()
        finally:
            conn_ccc.close()
    except Exception as exc:
        logger.error("circuit_capacity_monitor: CCC DB unavailable: %s", exc)
        result["status"] = "error"
        result["errors"].append(f"ccc_db: {exc}")
        return result

    warn: List[Dict] = []
    critical: List[Dict] = []

    for row in rows:
        result["circuits_checked"] += 1
        cid = row["circuit_id"] if hasattr(row, "keys") else row[0]
        carrier = row["carrier"] if hasattr(row, "keys") else row[1]
        bw = row["bandwidth_gbps"] if hasattr(row, "keys") else row[2]
        util = row["utilization_pct"] if hasattr(row, "keys") else row[3]

        entry = {"circuit_id": cid, "carrier": carrier,
                 "bandwidth_gbps": bw, "utilization_pct": util}

        if util >= CRITICAL_THRESHOLD_PCT:
            critical.append(entry)
        elif util >= WARN_THRESHOLD_PCT:
            warn.append(entry)

    result["warn_circuits"] = warn
    result["critical_circuits"] = critical

    if dry_run:
        return result

    # Publish canvas events
    try:
        from tools.canvas.event_bus import publish
        for c in critical:
            publish("ccc", "ccc.circuit.capacity_critical", {
                "circuit_id": c["circuit_id"],
                "carrier": c["carrier"],
                "utilization_pct": c["utilization_pct"],
                "threshold": CRITICAL_THRESHOLD_PCT,
            })
            result["events_published"] += 1
        for w in warn:
            publish("ccc", "ccc.circuit.capacity_warn", {
                "circuit_id": w["circuit_id"],
                "carrier": w["carrier"],
                "utilization_pct": w["utilization_pct"],
                "threshold": WARN_THRESHOLD_PCT,
            })
            result["events_published"] += 1
    except Exception as exc:
        result["errors"].append(f"event_bus: {exc}")

    # Create NOC alarms for critical circuits if NOCC is enabled
    if critical:
        try:
            from tools.noc_canvas.db.init_db import get_connection as nocc_conn
            conn_nocc = nocc_conn()
            try:
                for c in critical:
                    _maybe_insert_alarm(conn_nocc, c)
                    result["alarms_created"] += 1
                conn_nocc.commit()
            finally:
                conn_nocc.close()
        except Exception as exc:
            result["errors"].append(f"nocc_alarm: {exc}")

    return result


def _maybe_insert_alarm(conn, circuit: Dict) -> None:
    cid = circuit["circuit_id"]
    desc = (
        f"Circuit {cid} ({circuit['carrier']}) at "
        f"{circuit['utilization_pct']:.1f}% utilization "
        f"(threshold: {CRITICAL_THRESHOLD_PCT}%)"
    )
    # Suppress duplicate alarms for the same circuit that are still active
    existing = conn.execute(
        "SELECT id FROM noc_alarms WHERE circuit_id=? AND alarm_type='circuit' "
        "AND severity='critical' AND cleared=0 LIMIT 1",
        (cid,),
    ).fetchone()
    if existing:
        # Refresh last_seen
        try:
            conn.execute(
                "UPDATE noc_alarms SET last_seen=?, description=? WHERE id=?",
                (_now(), desc, existing[0]),
            )
        except Exception:
            conn.execute(
                "UPDATE noc_alarms SET last_seen=%s, description=%s WHERE id=%s",
                (_now(), desc, existing[0]),
            )
        return

    try:
        conn.execute(
            "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, "
            "circuit_id, description, first_seen, last_seen, cleared, suppressed, acknowledged) "
            "VALUES (?,?,?,?,?,?,?,?,0,0,0)",
            ("circuit_capacity_monitor", "critical", "circuit",
             f"CCC/{circuit['carrier']}", cid, desc, _now(), _now()),
        )
    except Exception:
        conn.execute(
            "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, "
            "circuit_id, description, first_seen, last_seen, cleared, suppressed, acknowledged) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,0)",
            ("circuit_capacity_monitor", "critical", "circuit",
             f"CCC/{circuit['carrier']}", cid, desc, _now(), _now()),
        )


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}), indent=2))
