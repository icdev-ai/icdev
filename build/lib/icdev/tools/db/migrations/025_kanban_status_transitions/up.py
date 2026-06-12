#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 025: Append-only audit log for kanban_tasks.status transitions.

Rationale: when a task's status changes, we want a forensic trail of
(when, from, to, who, why). No such log exists today; the E-gate
orphan-done incident (2026-04-15) left the investigator with no way to
tell whether the scheduler, a manual SQL UPDATE, or a phantom-completion
false-positive marked E-gate done.

Table: kanban_status_transitions
  id              TEXT PRIMARY KEY (random)
  task_id         TEXT NOT NULL (FK-like; not declared for cross-DB portability)
  from_status     TEXT       (nullable — first insert has no prior state)
  to_status       TEXT NOT NULL
  actor           TEXT       (free-form: 'scheduler', 'manual', 'orphan_sweep', etc.)
  reason          TEXT       (optional human note)
  recorded_at     TIMESTAMP NOT NULL (UTC ISO)

Policy: append-only. Per CLAUDE.md guardrail, add table to
APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.

Idempotent — uses sqlite_master check before CREATE.
"""

import sqlite3

MIGRATION_ID = "025"
MIGRATION_NAME = "kanban_status_transitions"
DESCRIPTION = "Append-only audit log for kanban_tasks.status transitions"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: create kanban_status_transitions (idempotent)."""
    actions = []
    cur = conn.cursor()

    if _table_exists(conn, "kanban_status_transitions"):
        actions.append("table_exists")
    else:
        cur.execute("""
            CREATE TABLE kanban_status_transitions (
                id           TEXT PRIMARY KEY,
                task_id      TEXT NOT NULL,
                from_status  TEXT,
                to_status    TEXT NOT NULL,
                actor        TEXT NOT NULL DEFAULT 'unknown',
                reason       TEXT,
                recorded_at  TEXT NOT NULL
            )
        """)
        actions.append("created_table")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_kst_task ON kanban_status_transitions (task_id, recorded_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_kst_time ON kanban_status_transitions (recorded_at DESC)"
    )
    actions.append("indexes_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
