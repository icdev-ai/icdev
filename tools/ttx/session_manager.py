# CUI // SP-CTI
"""TTX Engine — session lifecycle CRUD."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from .constants import SESSION_STATES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _join_code(n: int = 6) -> str:
    return secrets.token_urlsafe(n)[:n].upper()


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def create_session(
    scenario_slug: str,
    session_mode: str,
    facilitator_name: str,
    duration_minutes: int = 120,
    max_teams: int = 8,
    config: dict | None = None,
) -> dict[str, Any]:
    conn = get_connection()
    code = _join_code()
    cfg = json.dumps(config or {})
    conn.execute(
        """INSERT INTO ttx_sessions
           (scenario_slug, session_mode, state, facilitator_name,
            join_code, duration_minutes, max_teams, config_json, created_at)
           VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
        (scenario_slug, session_mode, facilitator_name,
         code, duration_minutes, max_teams, cfg, _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM ttx_sessions WHERE join_code = ?", (code,)
    ).fetchone()
    return dict(row)


def get_session(session_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM ttx_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def get_session_by_code(join_code: str) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM ttx_sessions WHERE join_code = ?", (join_code.upper(),)
    ).fetchone()
    return dict(row) if row else None


def list_sessions(state: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    if state:
        rows = conn.execute(
            "SELECT * FROM ttx_sessions WHERE state = ? ORDER BY created_at DESC",
            (state,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ttx_sessions ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


def update_session_state(session_id: int, new_state: str) -> dict[str, Any] | None:
    if new_state not in SESSION_STATES:
        raise ValueError(f"Invalid state: {new_state!r}")
    conn = get_connection()
    updates: list[tuple] = [(new_state, session_id)]
    if new_state == "active":
        conn.execute(
            "UPDATE ttx_sessions SET state = ?, started_at = ? WHERE session_id = ?",
            (new_state, _now(), session_id),
        )
    elif new_state == "ended":
        conn.execute(
            "UPDATE ttx_sessions SET state = ?, ended_at = ? WHERE session_id = ?",
            (new_state, _now(), session_id),
        )
    else:
        conn.execute(
            "UPDATE ttx_sessions SET state = ? WHERE session_id = ?",
            *updates,
        )
    conn.commit()
    return get_session(session_id)
