# CUI // SP-CTI
"""Weekly retrospective reflex — runs Friday 18:00 UTC."""
from __future__ import annotations
from datetime import datetime, timezone
from tools.logging.icdev_logger import get_logger
logger = get_logger(__name__)


def run(args: dict, _ctx) -> dict:
    now = datetime.now(timezone.utc)
    # Only run Friday (weekday=4) between 18:00–19:00 UTC
    if now.weekday() != 4 or now.hour != 18:
        return {
            "skipped": True,
            "reason": f"not Friday 18:00 UTC (weekday={now.weekday()} hour={now.hour})",
        }

    generated: list[str] = []
    errors: list[dict] = []

    try:
        from tools.second_brain.retro import generate_weekly_retro, get_all_users
        for user_id, tenant_id in get_all_users():
            try:
                retro = generate_weekly_retro(user_id, tenant_id)
                if retro:
                    generated.append(user_id)
                    logger.info("[weekly_retro] generated for user %s", user_id)
            except Exception as exc:
                errors.append({"user_id": user_id, "error": str(exc)})
                logger.warning("[weekly_retro] failed for %s: %s", user_id, exc)
    except Exception as exc:
        logger.error("[weekly_retro] reflex error: %s", exc)
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "generated": generated, "errors": errors}
