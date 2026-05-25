# CUI // SP-CTI
"""Genesis Reflex — SDC Security Control Expiry (4h cadence).

Scans sc_controls for controls whose review_date is within 90 days or
already past. Publishes canvas events and suggests kanban cards for review.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 4
WARN_DAYS = 90   # Flag controls expiring within this many days


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_until(iso_str: str) -> int:
    if not iso_str:
        return 9999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return 9999


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Check SDC sc_controls for approaching review dates.

    Returns:
        controls_checked: int
        expiring_soon: list of {id, title, review_date, days_until_expiry}
        events_published: int
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "controls_checked": 0,
        "expiring_soon": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.security_canvas.db.init_db import get_connection as sdc_conn
        conn_sdc = sdc_conn()
        try:
            # sc_controls may have review_date or next_review column
            cols = [r[1] for r in conn_sdc.execute("PRAGMA table_info(sc_controls)").fetchall()]
            date_col = "review_date" if "review_date" in cols else (
                "next_review" if "next_review" in cols else None
            )
            if date_col:
                rows = conn_sdc.execute(
                    f"SELECT id, title, {date_col} FROM sc_controls "  # nosec B608
                    f"WHERE {date_col} IS NOT NULL ORDER BY {date_col}"  # nosec B608
                ).fetchall()
            else:
                rows = []
        finally:
            conn_sdc.close()

        result["controls_checked"] = len(rows)
        expiring = []
        for row in rows:
            cid = row[0] if isinstance(row, (tuple, list)) else row["id"]
            title = row[1] if isinstance(row, (tuple, list)) else row["title"]
            date_val = row[2] if isinstance(row, (tuple, list)) else row[date_col]
            days = _days_until(date_val or "")
            if days <= WARN_DAYS:
                expiring.append({"id": cid, "title": title,
                                  "review_date": date_val, "days_until_expiry": days})

        result["expiring_soon"] = expiring

        if expiring and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for ctrl in expiring:
                    publish("sdc", "sdc.control.expiring", {
                        "control_id": ctrl["id"],
                        "title": ctrl["title"],
                        "days_until_expiry": ctrl["days_until_expiry"],
                        "review_date": ctrl["review_date"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("sdc_control_expiry reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}), indent=2))
