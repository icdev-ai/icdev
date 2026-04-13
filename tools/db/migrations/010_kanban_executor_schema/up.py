#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 010: Kanban Executor schema (RESTORED).

The original 010 migration files were lost from disk but the migration is
recorded in schema_migrations as applied (2026-04-04). This file restores
the migration as a fully idempotent re-run so a fresh checkout reproduces
the same schema.

What it adds:
  1. kanban_executions audit table (per-task execution record)
  2. kanban_tasks.executor_type / execution_id / executor_url columns

Both operations are idempotent: tables/columns are created only when
missing.
"""

import sqlite3

MIGRATION_ID = "010"
MIGRATION_NAME = "kanban_executor_schema"
DESCRIPTION = "Add kanban_executions table + executor columns on kanban_tasks"


_KANBAN_EXECUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_executions (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    executor_type   TEXT NOT NULL DEFAULT 'claude_cli',
    execution_id    TEXT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    exit_code       INTEGER,
    output_summary  TEXT,
    executor_url    TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


_KANBAN_TASKS_NEW_COLUMNS = [
    ("executor_type", "TEXT DEFAULT 'claude_cli'"),
    ("execution_id", "TEXT"),
    ("executor_url", "TEXT"),
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def up(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.executescript(_KANBAN_EXECUTIONS_DDL)

    columns_added = 0
    columns_skipped = 0
    if _table_exists(conn, "kanban_tasks"):
        for col, col_type in _KANBAN_TASKS_NEW_COLUMNS:
            if _column_exists(conn, "kanban_tasks", col):
                columns_skipped += 1
                continue
            try:
                cur.execute(f"ALTER TABLE kanban_tasks ADD COLUMN {col} {col_type}")
                columns_added += 1
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    columns_skipped += 1
                else:
                    raise
    conn.commit()
    return {
        "status": "applied",
        "table_kanban_executions": "ensured",
        "kanban_tasks_columns_added": columns_added,
        "kanban_tasks_columns_skipped": columns_skipped,
    }
