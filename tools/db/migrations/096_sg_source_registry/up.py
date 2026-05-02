#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 096 — Source Registry (STANAG 2022 reliability/credibility grades)."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_sources (
                id              TEXT PRIMARY KEY,
                source_name     TEXT NOT NULL,
                source_type     TEXT DEFAULT 'HUMINT'
                                    CHECK(source_type IN
                                      ('HUMINT','SIGINT','IMINT','OSINT',
                                       'MASINT','SOCMINT','technical','unknown')),
                reliability     TEXT DEFAULT 'F'
                                    CHECK(reliability IN ('A','B','C','D','E','F')),
                theater         TEXT DEFAULT 'global',
                notes           TEXT,
                last_reporting  TEXT,
                created_by      TEXT DEFAULT 'analyst',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_source_reports (
                id              TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL,
                content         TEXT,
                credibility     TEXT DEFAULT '6'
                                    CHECK(credibility IN ('1','2','3','4','5','6')),
                collected_at    TEXT NOT NULL,
                confidence      REAL DEFAULT 0.5,
                subject         TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_sources_theater "
            "ON sg_sources(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_reports_source "
            "ON sg_source_reports(source_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_reports_collected "
            "ON sg_source_reports(collected_at DESC)"
        )
        conn.commit()
        print("Migration 096 up: sg_sources + sg_source_reports created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
