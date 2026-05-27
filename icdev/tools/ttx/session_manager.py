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
    tenant_id: str | None = None,
) -> dict[str, Any]:
    conn = get_connection()
    code = _join_code()
    cfg = json.dumps(config or {})
    conn.execute(
        """INSERT INTO ttx_sessions
           (scenario_slug, session_mode, state, facilitator_name,
            join_code, duration_minutes, max_teams, config_json, created_at, tenant_id)
           VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
        (scenario_slug, session_mode, facilitator_name,
         code, duration_minutes, max_teams, cfg, _now(), tenant_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM ttx_sessions WHERE join_code = ?", (code,)
    ).fetchone()
    return dict(row)


def get_session(session_id: int, tenant_id: str | None = None) -> dict[str, Any] | None:
    conn = get_connection()
    if tenant_id is not None:
        row = conn.execute(
            "SELECT * FROM ttx_sessions WHERE session_id = ? AND tenant_id = ?",
            (session_id, tenant_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM ttx_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def get_session_by_code(join_code: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    conn = get_connection()
    if tenant_id is not None:
        row = conn.execute(
            "SELECT * FROM ttx_sessions WHERE join_code = ? AND tenant_id = ?",
            (join_code.upper(), tenant_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM ttx_sessions WHERE join_code = ?", (join_code.upper(),)
        ).fetchone()
    return dict(row) if row else None


def list_sessions(state: str | None = None, tenant_id: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    params: list[Any] = []
    clauses: list[str] = []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if tenant_id:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    else:
        clauses.append("(tenant_id IS NULL OR tenant_id = '')")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM ttx_sessions {where} ORDER BY created_at DESC LIMIT 100",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_session_state(session_id: int, new_state: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    if new_state not in SESSION_STATES:
        raise ValueError(f"Invalid state: {new_state!r}")
    conn = get_connection()
    # Verify tenant ownership before updating
    if tenant_id is not None:
        existing = conn.execute(
            "SELECT tenant_id FROM ttx_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing and existing["tenant_id"] != tenant_id:
            raise PermissionError("Session does not belong to this tenant")
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
            (new_state, session_id),
        )
    conn.commit()
    return get_session(session_id, tenant_id=tenant_id)
