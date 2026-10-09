# CUI // SP-CTI
"""BEFORE — raises ProgrammingError on PostgreSQL the first time it runs."""
from tools.db.storage import get_connection


def find_task(task_id: str):
    conn = get_connection()
    try:
        # `?` is SQLite syntax. psycopg2 rejects it.
        row = conn.execute(
            "SELECT id, title, status FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def recent_by_status(status: str, limit: int):
    conn = get_connection()
    try:
        # Same bug, and `%d` is wrong even on a driver that accepted it — there
        # is only ever one placeholder form.
        return conn.execute(
            "SELECT id FROM kanban_tasks WHERE status = ? ORDER BY updated_at DESC LIMIT %d",
            (status, limit),
        ).fetchall()
    finally:
        conn.close()
