#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 015: Add native task dependency support to kanban_tasks.

Replaces the park-in-scheduled workaround (tools/awareness/promote_next_phase.py)
with a proper depends_on column on kanban_tasks. The listener's _get_due_tasks
query is extended to exclude blocked tasks, the dashboard kanban API exposes
the dependency relationship, and the kanban template shows a "blocked by X"
badge.

Idempotent — uses PRAGMA table_info to detect whether the column already
exists before running ALTER TABLE.
"""

import sqlite3

MIGRATION_ID = "015"
MIGRATION_NAME = "kanban_depends_on"
DESCRIPTION = "Add depends_on_task_id column + index to kanban_tasks"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: add depends_on_task_id column + index."""
    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks missing"}

    cur = conn.cursor()
    actions = []

    if _has_column(conn, "kanban_tasks", "depends_on_task_id"):
        actions.append("column_exists")
    else:
        # SQLite doesn't support REFERENCES in ALTER TABLE ADD COLUMN, so we
        # add the column without a declared FK. The dashboard kanban API
        # validates the target exists at insert time (see create_task's
        # cycle detection). Logical FK only — no ON DELETE cascade needed;
        # a dropped parent just leaves the blocked task permanently blocked,
        # which matches operator expectations.
        cur.execute("ALTER TABLE kanban_tasks ADD COLUMN depends_on_task_id TEXT")
        actions.append("column_added")

    # Index is created unconditionally with IF NOT EXISTS
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_kanban_depends "
        "ON kanban_tasks(depends_on_task_id)"
    )
    actions.append("index_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
