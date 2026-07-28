# CUI // SP-CTI
"""AFTER — correct on PostgreSQL, and on SQLite via the storage layer."""
from tools.db.storage import get_connection


def find_task(task_id: str):
    conn = get_connection()
    try:
        # `%s` for every parameter, of every type. The driver handles typing.
        row = conn.execute(
            "SELECT id, title, status FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def recent_by_status(status: str, limit: int):
    conn = get_connection()
    try:
        # LIMIT is a parameter like any other — still %s, never %d.
        return conn.execute(
            "SELECT id FROM kanban_tasks WHERE status = %s "
            "ORDER BY updated_at DESC LIMIT %s",
            (status, limit),
        ).fetchall()
    finally:
        conn.close()
