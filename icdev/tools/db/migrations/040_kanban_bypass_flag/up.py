#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 040: Add completed_via_bypass flag to kanban_tasks.

Adds a boolean column ``completed_via_bypass INTEGER NOT NULL DEFAULT 0``
to ``kanban_tasks``. When a task is moved to ``done`` via the bypass path
(operator-supplied bypass_verification=true + bypass_reason), this flag
is set to 1 so the completion mode is queryable directly on the task row
without a JOIN to kanban_verifications.

Idempotent — skips the ALTER if the column already exists.
"""

import sqlite3

MIGRATION_ID = "040"
MIGRATION_NAME = "kanban_bypass_flag"
DESCRIPTION = "Add completed_via_bypass boolean flag to kanban_tasks"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
    return any(row[1] == column for row in rows)


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: add completed_via_bypass column (idempotent)."""
    actions = []

    if _column_exists(conn, "kanban_tasks", "completed_via_bypass"):
        actions.append("column_exists")
    else:
        conn.execute(
            "ALTER TABLE kanban_tasks "
            "ADD COLUMN completed_via_bypass INTEGER NOT NULL DEFAULT 0"
        )
        actions.append("column_added")

    conn.commit()
    return {"status": "applied", "actions": actions}
