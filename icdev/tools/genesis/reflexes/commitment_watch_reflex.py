# CUI // SP-CTI
"""Genesis reflex — daily commitment date watch across all customer relationships."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_TARGET_HOUR = 6  # UTC — fires before morning briefings


def run(args: dict, _ctx) -> dict:
    now = datetime.now(timezone.utc)
    if now.hour != _TARGET_HOUR:
        return {"skipped": True, "reason": f"Not {_TARGET_HOUR}:00 UTC (now={now.hour}:xx)"}

    try:
        from tools.second_brain.proactive_advisor import generate_commitment_alerts
        users = _get_active_users()
    except ImportError as exc:
        return {"skipped": True, "reason": str(exc)}

    if not users:
        return {"users_processed": 0}

    total_alerts = 0
    for u in users:
        try:
            alerts = generate_commitment_alerts(u["user_id"], u["tenant_id"])
            total_alerts += len(alerts)
        except Exception as exc:
            logger.warning("[commitment_watch] failed for %s: %s", u["user_id"], exc)

    return {"users_processed": len(users), "total_alerts": total_alerts}


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
