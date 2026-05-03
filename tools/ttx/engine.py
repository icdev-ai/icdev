# CUI // SP-CTI
"""TTX Engine — team creation, member management, and scenario orchestration."""
from __future__ import annotations

import random
import string
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _join_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


class TTXEngine:
    """Core game engine for TTX sessions."""

    def create_team(self, session_id: int, team_name: str) -> dict:
        conn = get_connection()
        conn.execute(
            "INSERT INTO ttx_teams (session_id, team_name, join_code, created_at) VALUES (?,?,?,?)",
            (session_id, team_name, _join_code(), _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ttx_teams WHERE session_id=? AND team_name=? ORDER BY team_id DESC LIMIT 1",
            (session_id, team_name),
        ).fetchone()
        return dict(row) if row else {}

    def join_team(self, team_id: int, player_name: str, role_id: str,
                  scenario_roles: list | None = None) -> dict:
        conn = get_connection()
        conn.execute(
            "INSERT INTO ttx_team_members (team_id, player_name, role_id, joined_at) "
            "VALUES (?,?,?,?)",
            (team_id, player_name, role_id, _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ttx_team_members WHERE team_id=? AND player_name=? ORDER BY member_id DESC LIMIT 1",
            (team_id, player_name),
        ).fetchone()
        return dict(row) if row else {}

    def get_session_state(self, session_id: int) -> dict:
        conn = get_connection()
        teams = [dict(r) for r in conn.execute(
            "SELECT * FROM ttx_teams WHERE session_id=? ORDER BY team_id", (session_id,)
        ).fetchall()]
        for t in teams:
            t["members"] = [dict(r) for r in conn.execute(
                "SELECT * FROM ttx_team_members WHERE team_id=?", (t["team_id"],)
            ).fetchall()]
        return {"teams": teams}
