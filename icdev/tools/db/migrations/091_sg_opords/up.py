#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 091 — OPORD Generator tables."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_opords (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                theater         TEXT DEFAULT 'unspecified',
                classification  TEXT DEFAULT 'CUI // SP-CTI',
                task_org        TEXT,
                situation       TEXT,
                mission         TEXT,
                execution       TEXT,
                sustainment     TEXT,
                command_signal  TEXT,
                status          TEXT DEFAULT 'draft'
                                    CHECK(status IN ('draft','approved','superseded')),
                created_by      TEXT DEFAULT 'analyst',
                approved_by     TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_opords_theater "
            "ON sg_opords(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_opords_created "
            "ON sg_opords(created_at DESC)"
        )
        conn.commit()
        print("Migration 091 up: sg_opords created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
