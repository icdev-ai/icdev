#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 012 rollback: revert kanban_tasks.id to INTEGER PK.

Provided for symmetry with migration_runner expectations. Use only if a
deployment depends on the legacy INTEGER schema (no known callers do — the
canonical schema in tests/conftest.py is TEXT). Rolling back will fail if any
row has a non-numeric id (e.g. a `task-abc123` UUID-style id), since INTEGER
PK columns reject non-integer values.
"""

import sqlite3


_OLD_TABLE_DDL = """
CREATE TABLE kanban_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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


def down(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, title, description, task_type, priority, status, "
        "scheduled_at, created_at, updated_at, completed_at, "
        "executor_type, execution_id, executor_url "
        "FROM kanban_tasks ORDER BY id"
    ).fetchall()

    # Validate all ids are numeric (or task-{hex}) before destroying the table.
    converted = []
    for r in rows:
        old_id = r[0]
        if isinstance(old_id, int):
            new_id = old_id
        elif isinstance(old_id, str) and old_id.startswith("task-"):
            try:
                new_id = int(old_id.split("-", 1)[1], 16)
            except ValueError as e:
                raise RuntimeError(
                    f"Cannot rollback: kanban_tasks row id={old_id!r} is not "
                    f"convertible to INTEGER ({e})"
                ) from e
        else:
            raise RuntimeError(
                f"Cannot rollback: kanban_tasks row id={old_id!r} is not numeric"
            )
        converted.append((new_id, *r[1:]))

    cur.execute("ALTER TABLE kanban_tasks RENAME TO kanban_tasks_new_012")
    cur.execute(_OLD_TABLE_DDL)
    for row in converted:
        cur.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, "
            "scheduled_at, created_at, updated_at, completed_at, "
            "executor_type, execution_id, executor_url) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
    cur.execute("DROP TABLE kanban_tasks_new_012")
    conn.commit()
    return {"status": "rolled_back", "rows": len(converted)}
