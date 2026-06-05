#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 119 — Add app_count and app_names to mc_migration_waves.

Adds two columns to mc_migration_waves in migration_canvas.db:
  app_count  INTEGER DEFAULT 0    — number of apps assigned to this wave
  app_names  TEXT    DEFAULT '[]' — JSON array of app names in this wave

Idempotent: skips ADD COLUMN if the column already exists.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATION_ID = "119"
MIGRATION_NAME = "mc_app_inventory"
DESCRIPTION = "Add app_count and app_names columns to mc_migration_waves"

_MC_DB_PATH = Path(__file__).resolve().parents[4] / "data" / "migration_canvas.db"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def run(conn=None) -> None:
    """Apply migration 119 to migration_canvas.db."""
    mc_conn = sqlite3.connect(str(_MC_DB_PATH))
    try:
        # mc_migration_waves lives in the separate migration_canvas.db, created
        # at runtime by the migration-canvas init. A fresh checkout (CI E2E job)
        # has no such file/table — sqlite3.connect creates an empty DB, so guard
        # rather than fail the chain with "no such table".
        if not _table_exists(mc_conn, "mc_migration_waves"):
            print(f"[migration-{MIGRATION_ID}] mc_migration_waves absent — skipping (created at runtime)")
            return
        if not _column_exists(mc_conn, "mc_migration_waves", "app_count"):
            mc_conn.execute(
                "ALTER TABLE mc_migration_waves ADD COLUMN app_count INTEGER DEFAULT 0"
            )
            print(f"[migration-{MIGRATION_ID}] Added app_count column")
        else:
            print(f"[migration-{MIGRATION_ID}] app_count already exists — skipping")

        if not _column_exists(mc_conn, "mc_migration_waves", "app_names"):
            mc_conn.execute(
                "ALTER TABLE mc_migration_waves ADD COLUMN app_names TEXT DEFAULT '[]'"
            )
            print(f"[migration-{MIGRATION_ID}] Added app_names column")
        else:
            print(f"[migration-{MIGRATION_ID}] app_names already exists — skipping")

        mc_conn.commit()
        print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")
    finally:
        mc_conn.close()


# Entry point when called by migration_runner (passes main icdev conn)
def up(conn) -> None:
    run(conn)


if __name__ == "__main__":
    run()
