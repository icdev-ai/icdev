# CUI // SP-CTI
"""Genesis reflex — hourly user-briefing generation and delivery (rri).

This reflex was REGISTERED in daemon.REFLEX_NAMES by the second-brain feature
(#64) with the documented intent "hourly check -> generate+deliver user
briefings", but its module was never created — so the daemon silently marked it
`is_stub / missing` and skipped it every cycle. Its engine
(`tools/second_brain/briefing.py`) and its sibling reflexes
(`nightly_prep_reflex`, `meeting_prep_reflex`) already existed; only the wrapper
was absent.

The rri card wired it (rather than deleting the entry) because the capability is
real and complete underneath — every piece it needs already ships:
`briefing.get_users_due_for_briefing`, `generate_briefing`, `deliver_briefing`.
Matches the `nightly_prep_reflex` shape exactly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Read by the daemon's stub-detector; this is a real implementation.
IMPLEMENTATION_STATUS = "full"


def run(args: dict, _ctx) -> dict:
    """Hourly: for users whose briefing_time hour matches now, generate + deliver.

    Best-effort and self-contained — a missing second_brain install or a single
    user's failure never aborts the reflex, matching every other reflex's
    degrade-not-crash posture.
    """
    current_hour = datetime.now(timezone.utc).hour

    try:
        from tools.second_brain.briefing import (
            deliver_briefing,
            generate_briefing,
            get_users_due_for_briefing,
        )
    except ImportError as exc:
        logger.warning("[daily_briefing_reflex] second_brain not available: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    due = get_users_due_for_briefing(current_hour)
    if not due:
        logger.debug("[daily_briefing_reflex] no users due at hour %d UTC", current_hour)
        return {"users_processed": 0, "hour_utc": current_hour}

    processed, delivered, errors = 0, 0, 0
    for entry in due:
        uid = entry["user_id"]
        tid = entry.get("tenant_id", "default")
        try:
            briefing = generate_briefing(uid, tenant_id=tid)
            if briefing and not briefing.get("error"):
                processed += 1
                result = deliver_briefing(uid, tenant_id=tid)
                if result and not result.get("error"):
                    delivered += 1
                logger.info("[daily_briefing_reflex] briefing generated for user %s", uid)
        except Exception as exc:  # noqa: BLE001 - one user's failure is not fatal
            errors += 1
            logger.warning("[daily_briefing_reflex] failed for user %s: %s", uid, exc)

    return {
        "users_processed": processed,
        "delivered": delivered,
        "errors": errors,
        "hour_utc": current_hour,
    }
