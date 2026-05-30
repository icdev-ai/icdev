#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 114 — Vibe-kanban Tier 2 DB schema.

Adds to kanban_tasks:
  branch_name    TEXT  — git branch name at completion (kanban/{task_id})
  commit_summary TEXT  — one-line-per-commit log of the merged branch

Creates:
  kanban_tags(id, name, color, created_at)         — tag palette
  kanban_task_tags(task_id, tag_id, created_at)    — many-to-many assignment
"""

MIGRATION_ID = "114"
MIGRATION_NAME = "kanban_vibe_tier2"
DESCRIPTION = "Vibe-kanban Tier 2: branch metrics + tag system"

_TAGS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_tags (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    color      TEXT NOT NULL DEFAULT '#6b7280',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_TASK_TAGS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_task_tags (
    task_id    TEXT NOT NULL,
    tag_id     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (task_id, tag_id),
    FOREIGN KEY (task_id) REFERENCES kanban_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)  REFERENCES kanban_tags(id)  ON DELETE CASCADE
)
"""

_TASK_TAGS_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_ktt_task_id ON kanban_task_tags (task_id)",
    "CREATE INDEX IF NOT EXISTS idx_ktt_tag_id  ON kanban_task_tags (tag_id)",
]

_KANBAN_TASKS_NEW_COLUMNS = [
    ("branch_name",    "TEXT"),
    ("commit_summary", "TEXT"),
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
        return row is not None
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
    return any((r[1] if isinstance(r, tuple) else dict(r).get("name")) == column for r in rows)


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    actions = []

    for col, col_type in _KANBAN_TASKS_NEW_COLUMNS:
        if not _column_exists(conn, "kanban_tasks", col):
            try:
                conn.execute(f"ALTER TABLE kanban_tasks ADD COLUMN {col} {col_type}")  # nosec B608
                actions.append(f"added_kanban_tasks.{col}")
            except Exception as exc:
                actions.append(f"skip_{col}: {exc}")
        else:
            actions.append(f"exists_{col}")

    conn.execute(_TAGS_DDL)
    actions.append("kanban_tags_table")

    conn.execute(_TASK_TAGS_DDL)
    actions.append("kanban_task_tags_table")

    for idx in _TASK_TAGS_IDX:
        conn.execute(idx)
    actions.append("indexes_created")

    conn.commit()
    return {"status": "applied", "actions": actions}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS kanban_task_tags")
    conn.execute("DROP TABLE IF EXISTS kanban_tags")
    conn.commit()
    return {"status": "rolled_back", "note": "branch_name/commit_summary columns left in place"}


if __name__ == "__main__":
    print(up())
