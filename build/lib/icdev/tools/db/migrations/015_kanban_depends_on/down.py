#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 015 rollback: drop depends_on_task_id column + index.

SQLite only supports DROP COLUMN from 3.35+. This rollback uses the
rename-copy-drop pattern for portability on older SQLite builds. Safe
to run on databases that never had the column — early-exits.
"""

import sqlite3

MIGRATION_ID = "015"
MIGRATION_NAME = "kanban_depends_on"


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


def down(conn: sqlite3.Connection) -> dict:
    """Drop the index + column added in 015."""
    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks missing"}

    cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS idx_kanban_depends")

    if not _has_column(conn, "kanban_tasks", "depends_on_task_id"):
        conn.commit()
        return {"status": "skipped", "reason": "column already gone"}

    # Use native DROP COLUMN if supported (SQLite >= 3.35).
    try:
        cur.execute("ALTER TABLE kanban_tasks DROP COLUMN depends_on_task_id")
        conn.commit()
        return {"status": "applied", "method": "drop_column"}
    except sqlite3.OperationalError:
        # Fall back to rename-copy-drop. Preserves every other column.
        cols = [
            r[1] for r in conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
            if r[1] != "depends_on_task_id"
        ]
        col_list = ", ".join(cols)
        cur.execute("ALTER TABLE kanban_tasks RENAME TO kanban_tasks_old_015")
        # Re-derive the current DDL for kanban_tasks by copying over everything
        # except the depends_on_task_id column. We intentionally avoid
        # hard-coding the full schema here — the canonical definition lives in
        # tools/db/init_icdev_db.py and may have drifted under later migrations.
        create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='kanban_tasks_old_015'"
        ).fetchone()[0]
        create_sql = create_sql.replace("kanban_tasks_old_015", "kanban_tasks")
        # Strip the depends_on_task_id column declaration from the CREATE TABLE.
        import re
        create_sql = re.sub(
            r",\s*depends_on_task_id\s+TEXT[^,)]*",
            "",
            create_sql,
        )
        cur.execute(create_sql)
        cur.execute(
            f"INSERT INTO kanban_tasks ({col_list}) "  # nosec B608 -- col_list derived from PRAGMA table_info, not user input
            f"SELECT {col_list} FROM kanban_tasks_old_015"
        )
        cur.execute("DROP TABLE kanban_tasks_old_015")
        conn.commit()
        return {"status": "applied", "method": "rename_copy_drop"}
