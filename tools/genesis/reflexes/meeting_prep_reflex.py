# CUI // SP-CTI
"""Genesis reflex — generate meeting prep cards for upcoming customer meetings."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def run(args: dict, _ctx) -> dict:
    try:
        from tools.second_brain.proactive_advisor import generate_meeting_preps
        users = _get_active_users()
    except ImportError as exc:
        return {"skipped": True, "reason": str(exc)}

    if not users:
        return {"users_processed": 0}

    processed = errors = 0
    for u in users:
        try:
            cards = generate_meeting_preps(u["user_id"], u["tenant_id"])
            if cards:
                processed += 1
        except Exception as exc:
            errors += 1
            logger.warning("[meeting_prep_reflex] failed for %s: %s", u["user_id"], exc)

    return {
        "users_processed": processed,
        "errors": errors,
        "hour_utc": datetime.now(timezone.utc).hour,
    }


def _get_active_users():
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            rows = conn.execute(
                "SELECT user_id, tenant_id FROM user_identity_profiles WHERE context_complete = 1"
            ).fetchall()
        return [{"user_id": r[0], "tenant_id": r[1] or "default"} for r in rows]
    except Exception as exc:
        logger.debug("[meeting_prep_reflex] user lookup failed: %s", exc)
        return []
