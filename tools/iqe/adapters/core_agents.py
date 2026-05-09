# CUI // SP-CTI
"""IQE core agents + projects collection adapters.

Importing this module registers two collections on the module-level Executor:
  agents.registry  — Agent registry (agents table)
  projects.list    — Project registry (projects table)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def agents_adapter(conn: Any) -> list[dict]:
    """Return rows from the agents table."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT id, name, status, url, last_heartbeat, created_at "
        "FROM agents ORDER BY name"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def projects_adapter(conn: Any) -> list[dict]:
    """Return rows from the projects table."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT id, name, type, status, classification, created_at, updated_at "
        "FROM projects ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("agents.registry", agents_adapter)
register_collection("projects.list", projects_adapter)
