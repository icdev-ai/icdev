#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 040 rollback: remove completed_via_bypass from kanban_tasks.

SQLite does not support DROP COLUMN before 3.35. For compatibility this
rollback recreates the table without the column (same approach as 012).
"""

import sqlite3

MIGRATION_ID = "040"
MIGRATION_NAME = "kanban_bypass_flag"


def down(conn: sqlite3.Connection) -> dict:
    """Rollback: drop completed_via_bypass column from kanban_tasks."""
    actions = []

    # Read current columns excluding the one to drop
    info = conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
    cols = [row[1] for row in info if row[1] != "completed_via_bypass"]

    if "completed_via_bypass" not in [row[1] for row in info]:
        return {"status": "skipped", "actions": ["column_not_present"]}

    col_list = ", ".join(cols)
    # nosec B608 — col_list is derived solely from PRAGMA table_info (internal
    # DB schema), never from external/user input; f-string is safe here.
    conn.executescript(f"""
        BEGIN;
        CREATE TABLE kanban_tasks_backup AS SELECT {col_list} FROM kanban_tasks;
        DROP TABLE kanban_tasks;
        ALTER TABLE kanban_tasks_backup RENAME TO kanban_tasks;
        COMMIT;
    """)  # nosec B608
    actions.append("column_dropped")
    return {"status": "rolled_back", "actions": actions}
