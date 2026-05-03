# CUI // SP-CTI
"""TTX Session Manager — CRUD for TTX game sessions."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(name: str, scenario_slug: str = "", max_teams: int = 8,
                   config: dict | None = None) -> dict:
    conn = get_connection()
    conn.execute(
        "INSERT INTO ttx_sessions (name, scenario_slug, config_json, status, max_teams, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (name, scenario_slug, json.dumps(config or {}), "open", max_teams, _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM ttx_sessions WHERE name=? ORDER BY session_id DESC LIMIT 1", (name,)
    ).fetchone()
    return dict(row) if row else {}


def get_session(session_id: int) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM ttx_sessions WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row else {}


def list_sessions(status: str | None = None) -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM ttx_sessions WHERE status=? ORDER BY session_id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ttx_sessions ORDER BY session_id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_session_status(session_id: int, status: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE ttx_sessions SET status=? WHERE session_id=?", (status, session_id))
    conn.commit()
