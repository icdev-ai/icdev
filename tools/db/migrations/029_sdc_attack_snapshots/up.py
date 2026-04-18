#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 029: sdc_attack_snapshots.

Resolves orphan_db_table gap detected by the Internal Awareness Engine:
tools/iqe/adapters/security.py references this table via SELECT but no
migration defined the schema, causing fresh deployments to 500 on first use.

Idempotent — IF NOT EXISTS guard on the table.
"""

import sqlite3

MIGRATION_ID = "029"
MIGRATION_NAME = "sdc_attack_snapshots"
DESCRIPTION = (
    "Create sdc_attack_snapshots table for the IQE security adapter "
    "(tools/iqe/adapters/security.py). Resolves orphan_db_table gap."
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
    """Apply migration: create sdc_attack_snapshots table (idempotent)."""
    actions = []
    cur = conn.cursor()

    if _table_exists(conn, "sdc_attack_snapshots"):
        actions.append("sdc_attack_snapshots:exists")
    else:
        cur.execute("""
            CREATE TABLE sdc_attack_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                component_id TEXT NOT NULL,
                nodes_json   TEXT DEFAULT '[]',
                edges_json   TEXT DEFAULT '[]',
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        actions.append("sdc_attack_snapshots:created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sdc_atk_component_id "
        "ON sdc_attack_snapshots(component_id)"
    )
    actions.append("indexes_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
