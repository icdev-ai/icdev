#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 031 rollback: drop DDC digital twin tables.

WARNING: Destructive — only run in development environments.
"""

import sqlite3

MIGRATION_ID = "031"
MIGRATION_NAME = "ddc_twin_tables"

_TABLES = ["data_twin_snapshots", "data_edges", "data_nodes"]


def down(conn: sqlite3.Connection) -> dict:
    """Drop DDC twin tables created by migration 031."""
    dropped = 0
    skipped = 0
    for table in _TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        dropped += 1
    conn.commit()
    return {"status": "applied", "dropped": dropped, "skipped": skipped}
