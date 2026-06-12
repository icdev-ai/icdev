#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 092 — Red Cell MLCOA/MDCOA analysis tables."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_red_cell_analyses (
                id              TEXT PRIMARY KEY,
                theater         TEXT DEFAULT 'global',
                scenario        TEXT,
                mlcoa_title     TEXT,
                mlcoa_rationale TEXT,
                mlcoa_prob      REAL DEFAULT 0.5,
                mlcoa_indicators TEXT,
                mlcoa_wargame   TEXT,
                mdcoa_title     TEXT,
                mdcoa_rationale TEXT,
                mdcoa_prob      REAL DEFAULT 0.3,
                mdcoa_indicators TEXT,
                mdcoa_wargame   TEXT,
                analyst_notes   TEXT,
                created_by      TEXT DEFAULT 'analyst',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_red_cell_theater "
            "ON sg_red_cell_analyses(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_red_cell_created "
            "ON sg_red_cell_analyses(created_at DESC)"
        )
        conn.commit()
        print("Migration 092 up: sg_red_cell_analyses created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
