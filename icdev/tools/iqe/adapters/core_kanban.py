# CUI // SP-CTI
"""IQE core Kanban collection adapters.

Importing this module registers two collections on the module-level Executor:
  kanban.tasks  — All Kanban tasks (kanban_tasks)
  kanban.epics  — Unique epics derived from kanban_tasks.id prefix grouping
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def tasks_adapter(conn: Any) -> list[dict]:
    """Return rows from kanban_tasks."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT id, title, task_type, priority, status, "
        "scheduled_at, created_at, updated_at, completed_at, "
        "executor_type, depends_on_task_id, failure_count, "
        "last_failure_reason "
        "FROM kanban_tasks ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def epics_adapter(conn: Any) -> list[dict]:
    """Return unique epic prefixes and task counts from kanban_tasks.id."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT id, title, task_type, priority, status, "
        "scheduled_at, created_at, updated_at, completed_at, "
        "executor_type, depends_on_task_id, failure_count "
        "FROM kanban_tasks ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    epics: dict[str, dict] = {}
    for row in rows:
        task_id = row.get("id") or ""
        parts = task_id.split("-")
        epic_key = "-".join(parts[:2]) if len(parts) >= 3 else task_id
        if epic_key not in epics:
            epics[epic_key] = {
                "epic_key": epic_key,
                "task_count": 0,
                "done_count": 0,
                "in_progress_count": 0,
            }
        epics[epic_key]["task_count"] += 1
        if row.get("status") == "completed":
            epics[epic_key]["done_count"] += 1
        elif row.get("status") == "in_progress":
            epics[epic_key]["in_progress_count"] += 1
    return list(epics.values())


register_collection("kanban.tasks", tasks_adapter)
register_collection("kanban.epics", epics_adapter)
