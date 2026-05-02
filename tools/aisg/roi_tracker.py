# CUI // SP-CTI
"""AISG ROI event emitter — records time-savings for automated actions."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.aisg.constants import ROI_RATES_MINUTES
from tools.db.storage import get_connection


def emit_roi_event(
    action_type: str,
    description: str,
    triggered_by: str = "system",
) -> int | None:
    """Insert one row into aisg_roi_events and return the new row id.

    Args:
        action_type: Must be one of the keys in ROI_RATES_MINUTES / DB CHECK.
        description: Human-readable summary of what triggered this event.
        triggered_by: Module or subsystem that caused the action.

    Returns:
        The inserted row id, or None if the insert failed silently.
    """
    time_saved = ROI_RATES_MINUTES.get(action_type)
    if time_saved is None:
        return None

    occurred_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO aisg_roi_events
                (action_type, time_saved_minutes, description, triggered_by, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (action_type, time_saved, description, triggered_by, occurred_at),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        return None
    finally:
        conn.close()
