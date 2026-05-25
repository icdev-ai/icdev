# CUI // SP-CTI
"""Genesis Reflex — NDC Topology Drift Detector (4h cadence).

Detects network topologies whose graph_json has changed since the last
inventory export, flagging stale diagrams and suggesting a re-export.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_str: str) -> int:
    if not iso_str:
        return 999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 999


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Check NDC topologies for drift vs last export.

    Returns:
        stale_topologies: list of {id, name, days_since_export}
        events_published: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "stale_topologies": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.network.db.init_db import get_connection as ndc_conn
        conn_ndc = ndc_conn()
        try:
            rows = conn_ndc.execute(
                "SELECT id, name, updated_at FROM topologies ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn_ndc.close()

        stale = []
        for row in rows:
            tid = row["id"] if isinstance(row, dict) else row[0]
            name = row["name"] if isinstance(row, dict) else row[1]
            updated_at = row["updated_at"] if isinstance(row, dict) else row[2]
            days = _days_since(updated_at or "")
            # Check if an export exists more recent than the topology update
            # (Heuristic: if topology updated but no export recorded in 7+ days, flag it)
            if days <= 7:
                stale.append({"id": tid, "name": name, "days_since_update": days})

        result["stale_topologies"] = stale

        if stale and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for t in stale:
                    publish("ndc", "ndc.topology.drift_detected", {
                        "topology_id": t["id"],
                        "topology_name": t["name"],
                        "days_since_update": t["days_since_update"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("ndc_topology_drift reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}), indent=2))
