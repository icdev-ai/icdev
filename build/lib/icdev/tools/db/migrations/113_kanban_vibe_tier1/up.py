#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 113 — Vibe-kanban Tier 1 adaptations.

Adds to kanban_tasks:
  start_date   TEXT  — planning start date (ISO date, nullable)
  target_date  TEXT  — delivery target date (ISO date, nullable)
  files_changed INT  — number of files changed by the worktree on completion
  lines_added   INT  — insertions from git diff stat
  lines_removed INT  — deletions from git diff stat

Creates:
  kanban_task_comments — collaborative notes per task (append-only per task)
"""

MIGRATION_ID = "113"
MIGRATION_NAME = "kanban_vibe_tier1"
DESCRIPTION = "Vibe-kanban Tier 1: planning dates, change metrics, task comments"

_COMMENTS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_task_comments (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT 'user',
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (task_id) REFERENCES kanban_tasks(id) ON DELETE CASCADE
)
"""

_COMMENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ktc_task_id ON kanban_task_comments (task_id)",
    "CREATE INDEX IF NOT EXISTS idx_ktc_created ON kanban_task_comments (created_at)",
]

_KANBAN_TASKS_NEW_COLUMNS = [
    ("start_date",    "TEXT"),
    ("target_date",   "TEXT"),
    ("files_changed", "INTEGER DEFAULT 0"),
    ("lines_added",   "INTEGER DEFAULT 0"),
    ("lines_removed", "INTEGER DEFAULT 0"),
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _column_exists(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            (table, column),
        ).fetchone()
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
        return any((r[1] if isinstance(r, tuple) else dict(r).get("name")) == column for r in rows)
    return row is not None


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    actions = []

    # 1. New columns on kanban_tasks
    for col, col_type in _KANBAN_TASKS_NEW_COLUMNS:
        if not _column_exists(conn, "kanban_tasks", col):
            try:
                conn.execute(f"ALTER TABLE kanban_tasks ADD COLUMN {col} {col_type}")  # nosec B608
                actions.append(f"added_kanban_tasks.{col}")
            except Exception as exc:
                actions.append(f"skip_kanban_tasks.{col}: {exc}")
        else:
            actions.append(f"exists_kanban_tasks.{col}")

    # 2. kanban_task_comments table
    conn.execute(_COMMENTS_DDL)
    actions.append("kanban_task_comments_table")
    for idx in _COMMENTS_INDEXES:
        conn.execute(idx)
    actions.append("indexes_created")

    conn.commit()
    return {"status": "applied", "actions": actions}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS kanban_task_comments")
    conn.commit()
    return {"status": "rolled_back", "note": "columns on kanban_tasks not removed (safe)"}


if __name__ == "__main__":
    result = up()
    print(result)
