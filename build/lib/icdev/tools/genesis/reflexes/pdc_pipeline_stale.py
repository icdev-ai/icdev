# CUI // SP-CTI
"""Genesis Reflex — PDC Pipeline Staleness Alert (6h cadence).

Flags pipelines in the Pipeline Design Canvas that have not been updated
in more than STALE_DAYS. Publishes canvas events for each stale pipeline.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 6
STALE_DAYS = 14  # Pipelines not updated in this many days are flagged


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
    """Identify pipelines with no update in STALE_DAYS days.

    Returns:
        pipelines_checked: int
        stale_pipelines: list of {id, name, days_since_update}
        events_published: int
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "pipelines_checked": 0,
        "stale_pipelines": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.pipeline.db.init_db import get_connection as pdc_conn
        conn_pdc = pdc_conn()
        try:
            rows = conn_pdc.execute(
                "SELECT id, name, updated_at FROM pipelines ORDER BY updated_at"
            ).fetchall()
        finally:
            conn_pdc.close()

        result["pipelines_checked"] = len(rows)
        stale = []
        for row in rows:
            pid = row["id"] if isinstance(row, dict) else row[0]
            name = row["name"] if isinstance(row, dict) else row[1]
            updated_at = row["updated_at"] if isinstance(row, dict) else row[2]
            days = _days_since(updated_at or "")
            if days >= STALE_DAYS:
                stale.append({"id": pid, "name": name, "days_since_update": days})

        result["stale_pipelines"] = stale

        if stale and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for p in stale:
                    publish("pdc", "pdc.pipeline.stale", {
                        "pipeline_id": p["id"],
                        "pipeline_name": p["name"],
                        "days_since_update": p["days_since_update"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("pdc_pipeline_stale reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}), indent=2))
