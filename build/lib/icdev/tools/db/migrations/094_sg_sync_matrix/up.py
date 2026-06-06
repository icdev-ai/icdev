#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 094 — Synchronization Matrix."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_sync_matrices (
                id              TEXT PRIMARY KEY,
                operation_name  TEXT NOT NULL,
                theater         TEXT DEFAULT 'unspecified',
                time_blocks     TEXT DEFAULT '[]',
                row_labels      TEXT DEFAULT '[]',
                cells           TEXT DEFAULT '{}',
                phase           TEXT DEFAULT 'planning',
                created_by      TEXT DEFAULT 'analyst',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_sync_theater "
            "ON sg_sync_matrices(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_sync_created "
            "ON sg_sync_matrices(created_at DESC)"
        )
        conn.commit()
        print("Migration 094 up: sg_sync_matrices created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
