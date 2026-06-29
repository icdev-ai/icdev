# CUI // SP-CTI
"""Genesis reflex — daily objective progress sync from kanban + git."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_TARGET_HOUR = 23  # UTC


def run(args: dict, _ctx) -> dict:
    now = datetime.now(timezone.utc)
    if now.hour != _TARGET_HOUR:
        return {"skipped": True, "reason": f"Not {_TARGET_HOUR}:00 UTC (now={now.hour}:xx)"}

    try:
        from tools.second_brain.objective_tracker import sync_objective_progress
        users = _get_active_users()
    except ImportError as exc:
        return {"skipped": True, "reason": str(exc)}

    if not users:
        return {"users_processed": 0}

    total_updated = 0
    for u in users:
        try:
            updated = sync_objective_progress(u["user_id"], u["tenant_id"])
            total_updated += len(updated)
        except Exception as exc:
            logger.warning("[objective_tracker_reflex] failed for %s: %s", u["user_id"], exc)

    return {"users_processed": len(users), "objectives_updated": total_updated}


def _get_active_users():
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            rows = conn.execute(
                "SELECT user_id, tenant_id FROM user_identity_profiles WHERE context_complete=1"
            ).fetchall()
        return [{"user_id": r[0], "tenant_id": r[1] or "default"} for r in rows]
    except Exception:
        return []
