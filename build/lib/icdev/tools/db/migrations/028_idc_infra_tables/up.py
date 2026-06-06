#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 028: idc_infra_resources + idc_infra_snapshots.

Resolves orphan_db_table gap detected by the Internal Awareness Engine:
tools/iqe/adapters/infra.py references both tables via SELECT/INSERT but
no migration defined the schema, causing fresh deployments to 500.

Idempotent — IF NOT EXISTS guards on both tables.
"""

import sqlite3

MIGRATION_ID = "028"
MIGRATION_NAME = "idc_infra_tables"
DESCRIPTION = (
    "Create idc_infra_resources and idc_infra_snapshots tables for the "
    "IQE infra adapter (tools/iqe/adapters/infra.py). Resolves orphan_db_table gap."
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: create IQE infra tables (idempotent)."""
    actions = []
    cur = conn.cursor()

    if _table_exists(conn, "idc_infra_resources"):
        actions.append("idc_infra_resources:exists")
    else:
        cur.execute("""
            CREATE TABLE idc_infra_resources (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                csp            TEXT NOT NULL,
                region         TEXT NOT NULL,
                resource_type  TEXT NOT NULL,
                resource_name  TEXT,
                classification TEXT DEFAULT 'UNCLASSIFIED',
                tags           TEXT,
                cost_per_month REAL DEFAULT 0.0,
                config         TEXT,
                created_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        actions.append("idc_infra_resources:created")

    if _table_exists(conn, "idc_infra_snapshots"):
        actions.append("idc_infra_snapshots:exists")
    else:
        cur.execute("""
            CREATE TABLE idc_infra_snapshots (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id    TEXT NOT NULL UNIQUE,
                taken_at       TEXT NOT NULL,
                csp            TEXT NOT NULL,
                region         TEXT NOT NULL,
                classification TEXT DEFAULT 'UNCLASSIFIED',
                resource_count INTEGER DEFAULT 0,
                baseline_hash  TEXT,
                notes          TEXT,
                created_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        actions.append("idc_infra_snapshots:created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_iir_csp_region "
        "ON idc_infra_resources(csp, region)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_iir_classification "
        "ON idc_infra_resources(classification)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_iis_snapshot_id "
        "ON idc_infra_snapshots(snapshot_id)"
    )
    actions.append("indexes_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
