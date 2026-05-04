#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 012: Convert kanban_tasks.id from INTEGER to TEXT PRIMARY KEY.

Schema drift fix. The canonical kanban_tasks schema (per tests/conftest.py
MINIMAL_ICDEV_SCHEMA, the dashboard API in tools/dashboard/api/kanban.py, the
genesis kanban reflex/scheduler, and the kanban_executions.task_id TEXT FK)
uses TEXT primary keys of the form `task-{uuid_hex_10}`. The production DB
drifted to `INTEGER PRIMARY KEY AUTOINCREMENT`, causing the API's
INSERT INTO kanban_tasks (id, ...) VALUES ('task-...', ...) to raise
"datatype mismatch" and silently break the entire dashboard kanban path.

This migration:
  1. Renames kanban_tasks → kanban_tasks_old
  2. Creates new kanban_tasks with id TEXT PRIMARY KEY (preserving every
     existing column the API/genesis reflex uses)
  3. Copies rows, converting integer ids to `task-{int:08x}` so existing
     references in kanban_executions.task_id remain resolvable
  4. Drops kanban_tasks_old
  5. Removes the obsolete sqlite_sequence row

Idempotent: detects current PK type and exits cleanly if TEXT is already in
place.
"""

import sqlite3

MIGRATION_ID = "012"
MIGRATION_NAME = "kanban_id_text_pk"
DESCRIPTION = "Convert kanban_tasks.id from INTEGER to TEXT PRIMARY KEY"


# Canonical column set — superset of conftest.py + columns added by 010 (executor)
# Order matches what production currently has so the INSERT INTO ... SELECT preserves data.
_NEW_TABLE_DDL = """
CREATE TABLE kanban_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT,
    task_type     TEXT DEFAULT 'build',
    priority      TEXT DEFAULT 'high',
    status        TEXT DEFAULT 'backlog',
    scheduled_at  TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at  TEXT,
    executor_type TEXT DEFAULT 'claude_cli',
    execution_id  TEXT,
    executor_url  TEXT
)
"""


def _id_column_type(conn: sqlite3.Connection) -> str:
    """Return the declared type of kanban_tasks.id, or '' if table is missing."""
    rows = conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
    for r in rows:
        # row tuple: (cid, name, type, notnull, dflt_value, pk)
        if r[1] == "id":
            return (r[2] or "").upper()
    return ""


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: rebuild kanban_tasks with TEXT primary key."""
    id_type = _id_column_type(conn)
    if not id_type:
        # Fresh DB without the table — nothing to migrate. The init script will
        # create the canonical TEXT-PK version separately.
        return {"status": "skipped", "reason": "kanban_tasks missing"}

    if "INT" not in id_type:
        # Already TEXT (or otherwise non-INTEGER) — nothing to do.
        return {"status": "skipped", "reason": f"id type already {id_type}"}

    cur = conn.cursor()

    # Snapshot existing rows for the migration result.
    existing = cur.execute(
        "SELECT id, title, description, task_type, priority, status, "
        "scheduled_at, created_at, updated_at, completed_at, "
        "executor_type, execution_id, executor_url "
        "FROM kanban_tasks ORDER BY id"
    ).fetchall()

    # Stage: rename old, create new, copy, drop old, fix sequence.
    cur.execute("ALTER TABLE kanban_tasks RENAME TO kanban_tasks_old_012")
    cur.execute(_NEW_TABLE_DDL)

    for row in existing:
        (
            old_id, title, description, task_type, priority, status,
            scheduled_at, created_at, updated_at, completed_at,
            executor_type, execution_id, executor_url,
        ) = row
        new_id = f"task-{int(old_id):08x}"
        cur.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, "
            "scheduled_at, created_at, updated_at, completed_at, "
            "executor_type, execution_id, executor_url) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id, title, description, task_type, priority, status,
                scheduled_at, created_at, updated_at, completed_at,
                executor_type, execution_id, executor_url,
            ),
        )

    cur.execute("DROP TABLE kanban_tasks_old_012")

    # The old AUTOINCREMENT counter is no longer relevant for a TEXT PK table.
    cur.execute("DELETE FROM sqlite_sequence WHERE name='kanban_tasks'")

    conn.commit()
    return {
        "status": "applied",
        "rows_migrated": len(existing),
        "id_format": "task-{int:08x}",
    }
