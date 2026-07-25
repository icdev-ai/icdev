# CUI // SP-CTI
"""Genesis reflex — generates and delivers daily briefings for all due users."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def run(args: dict, _ctx) -> dict:
    """Hourly reflex: generate + deliver briefings for users whose briefing_time matches now."""
    current_hour = datetime.now(timezone.utc).hour

    try:
        from tools.second_brain.briefing import (
            deliver_briefing,
            generate_briefing,
            get_todays_briefing,
            get_users_due_for_briefing,
        )
    except ImportError as exc:
        logger.warning("[daily_briefing_reflex] second_brain not available: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    due_users = get_users_due_for_briefing(current_hour)
    if not due_users:
        logger.debug("[daily_briefing_reflex] no users due at hour %d UTC", current_hour)
        return {"users_processed": 0, "hour_utc": current_hour}

    results = []
    for entry in due_users:
        uid = entry["user_id"]
        tid = entry["tenant_id"]
        try:
            # Idempotent — if already generated today, get_todays_briefing returns it
            briefing = get_todays_briefing(uid, tid)
            if briefing:
                logger.debug("[daily_briefing_reflex] briefing already exists for %s today", uid)
                delivery = deliver_briefing(uid, tenant_id=tid)
            else:
                briefing = generate_briefing(uid, tenant_id=tid)
                delivery = deliver_briefing(uid, tenant_id=tid)
            results.append({"user_id": uid, "status": "ok", "delivery": delivery})
        except Exception as exc:
            logger.warning("[daily_briefing_reflex] failed for %s: %s", uid, exc)
            results.append({"user_id": uid, "status": "error", "error": str(exc)})

    logger.info("[daily_briefing_reflex] processed %d users at hour %d UTC", len(results), current_hour)
    return {"users_processed": len(results), "hour_utc": current_hour, "results": results}
