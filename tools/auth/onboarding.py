# CUI // SP-CTI
"""Onboarding state management — per-user wizard progress."""
from __future__ import annotations

import json

from tools.db.storage import get_canvas_connection, sql_placeholder

_DEFAULT_STATE: dict = {
    "completed": False,
    "current_step": 0,
    "steps_done": [],
    "llm_configured": False,
    "canvas_enabled": None,
    "first_iqe_run": False,
    "dismissed_at": None,
    "last_seen_version": None,
}

_ALLOWED_KEYS = frozenset(_DEFAULT_STATE)


def get_onboarding_state(user_id: str) -> dict:
    """Return onboarding state for *user_id*, creating defaults if absent."""
    with get_canvas_connection("ICDEV_USER_PREFS_ENABLED") as conn:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT onboarding_state FROM user_preferences WHERE user_id = {ph}",
            (user_id,),
        ).fetchone()
    if row is None:
        return dict(_DEFAULT_STATE)
    try:
        state = json.loads(row[0] or "{}")
    except (ValueError, TypeError):
        state = {}
    return {**_DEFAULT_STATE, **state}


def update_onboarding_state(user_id: str, **kwargs) -> dict:
    """Merge *kwargs* into the stored state; unknown keys are ignored."""
    current = get_onboarding_state(user_id)
    for key, value in kwargs.items():
        if key in _ALLOWED_KEYS:
            current[key] = value
    blob = json.dumps(current)
    with get_canvas_connection("ICDEV_USER_PREFS_ENABLED") as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"""
            INSERT INTO user_preferences (user_id, onboarding_state)
                VALUES ({ph}, {ph})
            ON CONFLICT(user_id) DO UPDATE SET
                onboarding_state = excluded.onboarding_state,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, blob),
        )
        conn.commit()
    return current


def get_last_seen_version(user_id: str) -> str | None:
    """Return the last release version the user has acknowledged."""
    state = get_onboarding_state(user_id)
    return state.get("last_seen_version")


def set_last_seen_version(user_id: str, version: str) -> None:
    """Record *version* as the last release the user has seen."""
    update_onboarding_state(user_id, last_seen_version=version)


def mark_onboarding_complete(user_id: str) -> dict:
    """Mark the wizard as fully completed for *user_id*."""
    from datetime import datetime, timezone

    dismissed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return update_onboarding_state(
        user_id,
        completed=True,
        dismissed_at=dismissed_at,
    )
