#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 016: Back-fill kanban_tasks.source_prediction_id on sqlite.

Pre-existing drift. Phase 2 of the Internal Awareness Engine added this
column to Postgres imperatively via suggested_card_writer.py, but never
landed a matching migration, so any sqlite-backed deployment diverged.
Surfaced 2026-04-11 while verifying migration 015 (depends_on_task_id)
against sqlite — the dashboard API's LEFT JOIN references kt.source_prediction_id,
which existed in Postgres but was missing from sqlite, causing every
/api/kanban/tasks call to 500.

Idempotent. Safe on Postgres-initialized databases where the column is
already present — uses PRAGMA table_info to detect.
"""

import sqlite3

MIGRATION_ID = "016"
MIGRATION_NAME = "kanban_source_prediction_id"
DESCRIPTION = "Back-fill kanban_tasks.source_prediction_id on sqlite"


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
    """Apply migration: add source_prediction_id column if missing."""
    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks missing"}

    if _has_column(conn, "kanban_tasks", "source_prediction_id"):
        return {"status": "skipped", "reason": "column already present"}

    conn.execute("ALTER TABLE kanban_tasks ADD COLUMN source_prediction_id TEXT")
    conn.commit()
    return {"status": "applied", "action": "column_added"}
