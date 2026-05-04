#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 065: Create des_execution_events append-only audit table.

Rationale (DES dispatcher audit, 2026-04-28): records every dispatch,
completion, verification, and gate_override event emitted by the DES
execution engine. Append-only (no UPDATE/DELETE) per NIST AU requirements.

Guard: skipped if kanban_tasks is missing (pre-kanban environment).
"""

import sqlite3

MIGRATION_ID = "065"
MIGRATION_NAME = "des_execution_events"
DESCRIPTION = "Create des_execution_events append-only audit table"


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

    if _table_exists(conn, "des_execution_events"):
        actions.append("table_exists")
    else:
        cur.execute("""
            CREATE TABLE des_execution_events (
                id               TEXT PRIMARY KEY,
                task_id          TEXT NOT NULL,
                event_type       TEXT CHECK(event_type IN (
                                     'dispatch', 'completion',
                                     'verification', 'gate_override')),
                skill_invoked    TEXT,
                inputs_json      TEXT,
                outputs_json     TEXT,
                executor         TEXT,
                dispatch_source  TEXT,
                task_status      TEXT,
                duration_ms      INTEGER,
                verification_signals TEXT,
                queued_at        TEXT,
                occurred_at      TEXT NOT NULL,
                classification   TEXT DEFAULT 'CUI // SP-CTI'
            )
        """)
        actions.append("table_created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_des_task_id "
        "ON des_execution_events(task_id)"
    )
    actions.append("idx_des_task_id_ensured")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_des_occurred_at "
        "ON des_execution_events(occurred_at)"
    )
    actions.append("idx_des_occurred_at_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
