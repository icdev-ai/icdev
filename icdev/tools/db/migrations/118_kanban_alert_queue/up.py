#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 118 — kanban_alert_queue table.

Creates:
  kanban_alert_queue(id, task_id, event, severity, title, body, reason,
                     actor, created_at, retry_count)

This table is the local durable store for Kanban alert notifications.
_queue_alert_locally in icdev/tools/genesis/reflexes/kanban.py INSERTs rows
here when a task fails so alerts survive DB contention and can be retried.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "118"
MIGRATION_NAME = "kanban_alert_queue"
DESCRIPTION = "Create kanban_alert_queue table for durable local alert storage"

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS kanban_alert_queue (
    id          INTEGER PRIMARY KEY,
    task_id     TEXT    NOT NULL,
    event       TEXT    NOT NULL DEFAULT 'failed',
    severity    TEXT    NOT NULL DEFAULT 'warning',
    title       TEXT,
    body        TEXT,
    reason      TEXT,
    actor       TEXT,
    created_at  TEXT    NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0
)
"""

_DDL_PG = """
CREATE TABLE IF NOT EXISTS kanban_alert_queue (
    id          SERIAL PRIMARY KEY,
    task_id     TEXT    NOT NULL,
    event       TEXT    NOT NULL DEFAULT 'failed',
    severity    TEXT    NOT NULL DEFAULT 'warning',
    title       TEXT,
    body        TEXT,
    reason      TEXT,
    actor       TEXT,
    created_at  TEXT    NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_kaq_task_id    ON kanban_alert_queue (task_id)",
    "CREATE INDEX IF NOT EXISTS idx_kaq_created_at ON kanban_alert_queue (created_at)",
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    ddl = _DDL_PG if _is_pg(conn) else _DDL_SQLITE
    conn.execute(ddl)
    for idx in _INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["kanban_alert_queue_table", "indexes_created"]}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS kanban_alert_queue")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.split("icdev")[0])
    result = up()
    print(result)
