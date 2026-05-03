# CUI // SP-CTI
"""TTX Session Manager — CRUD for TTX game sessions."""
from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _join_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _ensure_name_column() -> None:
    """Add `name` column to ttx_sessions if absent (one-time migration)."""
    try:
        conn = get_connection()
        exists = conn.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name='ttx_sessions' AND column_name='name'
        """).fetchone()
        if not exists:
            conn.execute("SET lock_timeout = '3s'")
            conn.execute("ALTER TABLE ttx_sessions ADD COLUMN name TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass  # column already exists, lock timeout, or unsupported — fine


# Run once at module import
_ensure_name_column()


def create_session(name: str, scenario_slug: str = "", max_teams: int = 8,
                   config: dict | None = None) -> dict:
    conn = get_connection()
    conn.execute(
        "INSERT INTO ttx_sessions (name, scenario_slug, config_json, state, max_teams, created_at, join_code) "
        "VALUES (?,?,?,?,?,?,?)",
        (name, scenario_slug, json.dumps(config or {}), "open", max_teams, _now(), _join_code()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM ttx_sessions WHERE name=? ORDER BY session_id DESC LIMIT 1", (name,)
    ).fetchone()
    return dict(row) if row else {}


def get_session(session_id: int) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM ttx_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        return {}
    d = dict(row)
    # Normalise: expose 'status' alias for 'state' for code that uses either name
    d.setdefault("status", d.get("state", "open"))
    return d


def list_sessions(status: str | None = None) -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM ttx_sessions WHERE state=? ORDER BY session_id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ttx_sessions ORDER BY session_id DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d.setdefault("status", d.get("state", "open"))
        result.append(d)
    return result


def update_session_status(session_id: int, status: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE ttx_sessions SET state=? WHERE session_id=?", (status, session_id))
    conn.commit()
