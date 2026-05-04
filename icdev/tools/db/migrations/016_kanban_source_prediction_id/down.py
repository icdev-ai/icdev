#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 016 rollback: drop kanban_tasks.source_prediction_id on sqlite.

Uses native ALTER TABLE DROP COLUMN on SQLite 3.35+, falls back to
rename-copy-drop on older builds.
"""

import sqlite3

MIGRATION_ID = "016"
MIGRATION_NAME = "kanban_source_prediction_id"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def down(conn: sqlite3.Connection) -> dict:
    """Drop the column added in 016."""
    if not _has_column(conn, "kanban_tasks", "source_prediction_id"):
        return {"status": "skipped", "reason": "column already gone"}

    try:
        conn.execute("ALTER TABLE kanban_tasks DROP COLUMN source_prediction_id")
        conn.commit()
        return {"status": "applied", "method": "drop_column"}
    except sqlite3.OperationalError:
        cols = [
            r[1]
            for r in conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
            if r[1] != "source_prediction_id"
        ]
        col_list = ", ".join(cols)
        conn.execute("ALTER TABLE kanban_tasks RENAME TO kanban_tasks_old_016")
        create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='kanban_tasks_old_016'"
        ).fetchone()[0]
        create_sql = create_sql.replace("kanban_tasks_old_016", "kanban_tasks")
        import re

        create_sql = re.sub(
            r",\s*source_prediction_id\s+TEXT[^,)]*",
            "",
            create_sql,
        )
        conn.execute(create_sql)
        conn.execute(
            f"INSERT INTO kanban_tasks ({col_list}) "  # nosec B608 -- col_list from PRAGMA, not user input
            f"SELECT {col_list} FROM kanban_tasks_old_016"
        )
        conn.execute("DROP TABLE kanban_tasks_old_016")
        conn.commit()
        return {"status": "applied", "method": "rename_copy_drop"}
