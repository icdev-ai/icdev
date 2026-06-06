#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 071: Create product_intel_runs append-only audit table.

Rationale (Product Intel Engine, 2026-04-28): records every run of the
product intelligence pipeline — engines invoked, signals collected, gaps
detected, dossiers produced, and federation routes resolved. Append-only
(no UPDATE/DELETE) per NIST AU requirements.

Guard: skipped if table already exists.
"""

import sqlite3

MIGRATION_ID = "071"
MIGRATION_NAME = "product_intel_runs"
DESCRIPTION = "Create product_intel_runs append-only audit table"


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
    cur = conn.cursor()

    if _table_exists(conn, "product_intel_runs"):
        actions.append("table_exists")
    else:
        cur.execute("""
            CREATE TABLE product_intel_runs (
                id                  TEXT PRIMARY KEY,
                started_at          TEXT,
                completed_at        TEXT,
                engines_run         TEXT,
                engines_failed      TEXT,
                total_signals       INTEGER,
                total_gaps          INTEGER,
                total_dossiers      INTEGER,
                federation_routes   INTEGER,
                result_json         TEXT,
                status              TEXT,
                classification      TEXT DEFAULT 'CUI // SP-CTI'
            )
        """)
        actions.append("table_created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_pi_runs_started_at "
        "ON product_intel_runs(started_at)"
    )
    actions.append("idx_pi_runs_started_at_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
