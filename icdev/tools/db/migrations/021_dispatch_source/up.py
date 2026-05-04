#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 021: Add dispatch_source column for traceability.

Allowed values:
  - 'genesis_scheduler' — kanban reflex auto-dispatch (headless Claude CLI)
  - 'claude_interactive' — user-driven Claude Code session
  - 'user_manual'        — direct shell commits (no Claude involvement)
  - 'unknown'            — pre-migration rows, or source couldn't be determined
"""

MIGRATION_ID = "021"
MIGRATION_NAME = "dispatch_source"
DESCRIPTION = "Add dispatch_source to kanban_tasks + kanban_verifications"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _has_column(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? AND column_name=?",
            (table, column),
        ).fetchone()
        return bool(row)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(
        (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == column
        for r in rows
    )


def up(conn) -> dict:
    actions = []

    for table in ("kanban_tasks", "kanban_verifications"):
        if not _has_column(conn, table, "dispatch_source"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN dispatch_source TEXT DEFAULT 'unknown'"
            )
            actions.append(f"added_dispatch_source_to_{table}")

    # Indexes for pattern analysis (query "show all phantom dones by scheduler")
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kanban_tasks_dispatch_source "
            "ON kanban_tasks (dispatch_source)"
        )
        actions.append("kanban_tasks_dispatch_index")
    except Exception as exc:
        actions.append(f"kanban_tasks_index_skipped: {exc}")

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kanban_verifications_dispatch_source "
            "ON kanban_verifications (dispatch_source)"
        )
        actions.append("kanban_verifications_dispatch_index")
    except Exception as exc:
        actions.append(f"kanban_verifications_index_skipped: {exc}")

    conn.commit()
    return {"status": "applied", "actions": actions}
