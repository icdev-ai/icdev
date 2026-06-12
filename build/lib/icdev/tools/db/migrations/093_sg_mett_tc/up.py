#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 093 — METT-TC worksheet tables."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_mett_tc (
                id                  TEXT PRIMARY KEY,
                theater             TEXT DEFAULT 'unspecified',
                operation_name      TEXT,
                mission             TEXT,
                enemy_situation     TEXT,
                terrain_weather     TEXT,
                troops_available    TEXT,
                time_available      TEXT,
                civil_considerations TEXT,
                auto_populated      INTEGER DEFAULT 0,
                created_by          TEXT DEFAULT 'analyst',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_mett_tc_theater "
            "ON sg_mett_tc(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_mett_tc_created "
            "ON sg_mett_tc(created_at DESC)"
        )
        conn.commit()
        print("Migration 093 up: sg_mett_tc created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
