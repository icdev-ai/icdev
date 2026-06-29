# CUI // SP-CTI
"""Genesis reflex — weekly architecture intelligence digest for all users with context_complete=1.

Runs weekly on Monday morning (UTC 07:00) to have the digest ready when users start their week.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Day 0=Monday; only fire on Monday
_TARGET_DOW = 0
# UTC hour to fire
_TARGET_HOUR = 7


def run(args: dict, _ctx) -> dict:
    """Weekly Monday digest: generate architecture intelligence per user."""
    now = datetime.now(timezone.utc)

    # Only execute on Monday at the target hour (genesis calls hourly)
    if now.weekday() != _TARGET_DOW or now.hour != _TARGET_HOUR:
        return {
            "skipped": True,
            "reason": f"Not Monday 07:00 UTC (now={now.strftime('%A %H:%M UTC')})",
        }

    try:
        from tools.second_brain.proactive_advisor import generate_weekly_architecture_digest
        users = _get_all_active_users()
    except ImportError as exc:
        logger.warning("[thought_leadership_reflex] second_brain not available: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    if not users:
        return {"users_processed": 0}

    processed, errors = 0, 0
    for entry in users:
        uid = entry["user_id"]
        tid = entry.get("tenant_id", "default")
        try:
            digest = generate_weekly_architecture_digest(uid, tid)
            if digest:
                processed += 1
                logger.info(
                    "[thought_leadership_reflex] digest generated for user %s (%d topics)",
                    uid, len(digest.get("topics", [])),
                )
        except Exception as exc:
            errors += 1
            logger.warning("[thought_leadership_reflex] failed for user %s: %s", uid, exc)

    return {
        "users_processed": processed,
        "errors": errors,
        "week_of": now.strftime("%Y-%m-%d"),
    }


def _get_all_active_users() -> list[dict]:
    """Return all users with a completed profile."""
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            rows = conn.execute(
                "SELECT user_id, tenant_id FROM user_identity_profiles WHERE context_complete = 1",
            ).fetchall()
        return [{"user_id": r[0], "tenant_id": r[1] or "default"} for r in rows]
    except Exception as exc:
        logger.debug("[thought_leadership_reflex] user lookup failed: %s", exc)
        return []
