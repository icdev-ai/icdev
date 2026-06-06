#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 026: Add dispatch_attempt_id to kanban_tasks for idempotency.

Rationale (Batch 4 V&V hardening, 2026-04-15): if the scheduler crashes
mid-dispatch, restart recovery resets in_progress→backlog but has no
idempotency key to detect if a prior Popen() is still running. Adding a
per-dispatch UUID prevents double-dispatch and lets verify_task_completed
ignore stale worker output from a prior attempt.

Column: ``dispatch_attempt_id TEXT`` (nullable; set at dispatch,
cleared at done/backlog transition).

Idempotent via PRAGMA table_info check.
"""

import sqlite3

MIGRATION_ID = "026"
MIGRATION_NAME = "kanban_dispatch_attempt"
DESCRIPTION = "Add dispatch_attempt_id column for idempotent restart semantics"


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
    actions = []
    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks missing"}

    cur = conn.cursor()
    if _has_column(conn, "kanban_tasks", "dispatch_attempt_id"):
        actions.append("column_exists")
    else:
        cur.execute(
            "ALTER TABLE kanban_tasks ADD COLUMN dispatch_attempt_id TEXT"
        )
        actions.append("column_added")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_kanban_dispatch_attempt "
        "ON kanban_tasks(dispatch_attempt_id)"
    )
    actions.append("index_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
