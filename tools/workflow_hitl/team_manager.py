# CUI // SP-CTI
"""Team, member, and assignment CRUD for HITL Workflow Management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Teams ─────────────────────────────────────────────────────────────────────

def create_team(
    name: str,
    description: str | None = None,
    canvas_type: str | None = None,
    created_by: str | None = None,
) -> str:
    team_id = f"wft-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO wf_teams (id, name, description, canvas_type, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (team_id, name, description, canvas_type, created_by, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return team_id
    finally:
        conn.close()


def get_team(team_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wf_teams WHERE id=%s", (team_id,)).fetchone()
        if not row:
            return None
        team = dict(row)
        members = conn.execute(
            "SELECT * FROM wf_team_members WHERE team_id=%s ORDER BY created_at",
            (team_id,),
        ).fetchall()
        team["members"] = [dict(m) for m in members]
        return team
    finally:
        conn.close()


def list_teams(canvas_type: str | None = None) -> list:
    conn = get_connection()
    try:
        if canvas_type:
            rows = conn.execute(
                "SELECT * FROM wf_teams WHERE canvas_type=%s OR canvas_type IS NULL ORDER BY name",
                (canvas_type,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM wf_teams ORDER BY name").fetchall()
        teams = []
        for r in rows:
            t = dict(r)
            t["member_count"] = conn.execute(
                "SELECT COUNT(*) FROM wf_team_members WHERE team_id=%s", (t["id"],)
            ).fetchone()[0]
            teams.append(t)
        return teams
    finally:
        conn.close()


# ── Members ───────────────────────────────────────────────────────────────────

def add_member(team_id: str, user_id: str, role_label: str) -> str:
    member_id = f"wfm-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wf_team_members (id, team_id, user_id, role_label, created_at) VALUES (%s,%s,%s,%s,%s)",
            (member_id, team_id, user_id, role_label, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return member_id
    finally:
        conn.close()


def remove_member(team_id: str, user_id: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM wf_team_members WHERE team_id=%s AND user_id=%s", (team_id, user_id))
        try:
            conn.commit()
        except Exception:
            pass
        return True
    finally:
        conn.close()


def get_members_by_role(team_id: str, role_label: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_team_members WHERE team_id=%s AND role_label=%s",
            (team_id, role_label),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Assignments ───────────────────────────────────────────────────────────────

def assign_team(
    team_id: str,
    scope_type: str,
    scope_id: str,
    template_id: str | None = None,
) -> str:
    assign_id = f"wfa-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO wf_team_assignments
               (id, team_id, template_id, scope_type, scope_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (assign_id, team_id, template_id, scope_type, scope_id, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return assign_id
    finally:
        conn.close()


def list_assignments(team_id: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_team_assignments WHERE team_id=%s ORDER BY created_at",
            (team_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_team(task_id: str) -> dict | None:
    """Find the team + template assigned to a given task.

    Resolution order: task → project → task_group (via tags).
    Returns {team, template} or None if no assignment found.
    """
    conn = get_connection()
    try:
        # Direct task assignment
        row = conn.execute(
            """SELECT ta.*, t.* FROM wf_team_assignments ta
               JOIN wf_teams t ON t.id = ta.team_id
               WHERE ta.scope_type='task' AND ta.scope_id=%s LIMIT 1""",
            (task_id,),
        ).fetchone()
        if row:
            return _build_resolve_result(conn, dict(row))

        # Try to get project_id from kanban_tasks
        task_row = conn.execute(
            "SELECT project_id, tags FROM kanban_tasks WHERE id=%s", (task_id,)
        ).fetchone()
        if not task_row:
            return None

        # Project-level assignment
        if task_row[0]:
            row = conn.execute(
                """SELECT ta.*, t.* FROM wf_team_assignments ta
                   JOIN wf_teams t ON t.id = ta.team_id
                   WHERE ta.scope_type='project' AND ta.scope_id=%s LIMIT 1""",
                (task_row[0],),
            ).fetchone()
            if row:
                return _build_resolve_result(conn, dict(row))

        # Task-group tag assignment
        tags_raw = task_row[1]
        if tags_raw:
            try:
                import json
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
                for tag in tags:
                    row = conn.execute(
                        """SELECT ta.*, t.* FROM wf_team_assignments ta
                           JOIN wf_teams t ON t.id = ta.team_id
                           WHERE ta.scope_type='task_group' AND ta.scope_id=%s LIMIT 1""",
                        (tag,),
                    ).fetchone()
                    if row:
                        return _build_resolve_result(conn, dict(row))
            except Exception:
                pass
        return None
    finally:
        conn.close()


def _build_resolve_result(conn, row: dict) -> dict:
    from tools.workflow_hitl import template_manager
    template_id = row.get("template_id")
    template = template_manager.get(template_id) if template_id else None
    return {"team": row, "template": template}
