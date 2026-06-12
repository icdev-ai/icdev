#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 085 — sg_ccir_trigger_events table.

Records automated trigger events when a CCIR condition is satisfied by
incoming signal data (SIGINT, EO, SOCMINT). Append-only (NIST AU).
"""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_ccir_trigger_events (
                id          TEXT PRIMARY KEY,
                ccir_id     TEXT NOT NULL,
                signal_source TEXT,
                signal_text TEXT,
                match_score REAL DEFAULT 0.0,
                resolved    INTEGER DEFAULT 0,
                resolved_at TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ccir_trigger_ccir_id "
            "ON sg_ccir_trigger_events(ccir_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ccir_trigger_created "
            "ON sg_ccir_trigger_events(created_at DESC)"
        )
        conn.commit()
        print("Migration 085 up: sg_ccir_trigger_events created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
