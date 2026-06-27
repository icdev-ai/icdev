# CUI // SP-CTI
"""TTX Engine — team formation and roster management.

Primary purpose: Create and manage teams and player rosters within a TTX (Tabletop
Exercise) game session. Writes to ttx_teams and ttx_team_members DB tables.

Public interface:
  create_team(session_id, team_name)       -> dict  — create team, returns row with join_code
  get_team(team_id)                        -> dict|None
  get_team_by_code(team_code)              -> dict|None
  list_teams(session_id)                   -> list[dict]
  add_member(team_id, player_name, role_id, persona=None) -> dict
  list_members(team_id)                    -> list[dict]
  update_team_score(team_id, delta)        -> None  — increment/decrement total_score
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _team_code(n: int = 6) -> str:
    return secrets.token_urlsafe(n)[:n].upper()


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def create_team(session_id: int, team_name: str) -> dict[str, Any]:
    conn = get_connection()
    code = _team_code()
    conn.execute(
        """INSERT INTO ttx_teams
           (session_id, team_name, join_code, total_score, rank_pos, created_at)
           VALUES (%s, %s, %s, 0, 0, %s)""",
        (session_id, team_name, code, _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM ttx_teams WHERE session_id = %s AND join_code = %s",
        (session_id, code),
    ).fetchone()
    return dict(row)


def get_team(team_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM ttx_teams WHERE team_id = %s", (team_id,)
    ).fetchone()
    return dict(row) if row else None


def get_team_by_code(team_code: str) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM ttx_teams WHERE join_code = %s", (team_code.upper(),)
    ).fetchone()
    return dict(row) if row else None


def list_teams(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ttx_teams WHERE session_id = %s ORDER BY rank_pos, created_at",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def add_member(
    team_id: int,
    player_name: str,
    role_id: str,
    persona: dict | None = None,
) -> dict[str, Any]:
    conn = get_connection()
    conn.execute(
        """INSERT INTO ttx_team_members
           (team_id, player_name, role_id, persona_json, joined_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (team_id, player_name, role_id, json.dumps(persona or {}), _now()),
    )
    conn.commit()
    row = conn.execute(
        """SELECT * FROM ttx_team_members
           WHERE team_id = %s AND player_name = %s
           ORDER BY joined_at DESC LIMIT 1""",
        (team_id, player_name),
    ).fetchone()
    return dict(row)


def list_members(team_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ttx_team_members WHERE team_id = %s ORDER BY joined_at",
        (team_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_team_score(team_id: int, delta: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE ttx_teams SET total_score = total_score + %s WHERE team_id = %s",
        (delta, team_id),
    )
    conn.commit()
