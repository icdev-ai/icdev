# CUI // SP-CTI
"""Genesis reflex — generates tomorrow-prep briefing sections for users whose work_end
falls within the current UTC hour."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def run(args: dict, _ctx) -> dict:
    """Evening reflex: for users whose work_end hour matches now, generate tomorrow prep."""
    current_hour = datetime.now(timezone.utc).hour

    try:
        from tools.second_brain.proactive_advisor import generate_tomorrow_prep
        users = _get_users_due_for_prep(current_hour)
    except ImportError as exc:
        logger.warning("[nightly_prep_reflex] second_brain not available: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    if not users:
        logger.debug("[nightly_prep_reflex] no users due at hour %d UTC", current_hour)
        return {"users_processed": 0, "hour_utc": current_hour}

    processed, errors = 0, 0
    for entry in users:
        uid = entry["user_id"]
        tid = entry.get("tenant_id", "default")
        try:
            prep = generate_tomorrow_prep(uid, tid)
            if prep:
                processed += 1
                logger.info("[nightly_prep_reflex] prep generated for user %s", uid)
        except Exception as exc:
            errors += 1
            logger.warning("[nightly_prep_reflex] failed for user %s: %s", uid, exc)

    return {
        "users_processed": processed,
        "errors": errors,
        "hour_utc": current_hour,
    }


def _get_users_due_for_prep(current_hour: int) -> list[dict]:
    """Return users whose `work_end` hour (UTC-approximated) matches *current_hour*."""
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            rows = conn.execute(
                """
                SELECT user_id, tenant_id, work_end, timezone
                FROM user_identity_profiles
                WHERE context_complete = 1
                """,
            ).fetchall()
        due = []
        for user_id, tenant_id, work_end, tz in rows:
            # work_end is stored as "HH:MM" in user's local time; compare hour only
            try:
                end_hour = int((work_end or "18:00").split(":")[0])
                # Rough UTC offset via tzdata (falls back to raw hour match)
                utc_hour = _local_to_utc_hour(end_hour, tz or "UTC")
                if utc_hour == current_hour:
                    due.append({"user_id": user_id, "tenant_id": tenant_id or "default"})
            except (ValueError, TypeError):
                pass
        return due
    except Exception as exc:
        logger.debug("[nightly_prep_reflex] user lookup failed: %s", exc)
        return []


def _local_to_utc_hour(local_hour: int, tz_name: str) -> int:
    """Approximate UTC hour for a local hour given a timezone name string."""
    try:
        import zoneinfo
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        zone = zoneinfo.ZoneInfo(tz_name)
        local_now = now.astimezone(zone)
        offset_hours = int(local_now.utcoffset().total_seconds() // 3600)
        return (local_hour - offset_hours) % 24
    except Exception:
        return local_hour
