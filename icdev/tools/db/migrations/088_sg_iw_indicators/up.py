#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 088 — Indicator and Warning (I&W) board tables.

sg_iw_indicators    — named indicators mapped to adversary COAs
sg_iw_observations  — analyst observations against each indicator
"""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_iw_indicators (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT,
                coa_id      TEXT NOT NULL,
                coa_name    TEXT NOT NULL,
                weight      REAL DEFAULT 1.0,
                category    TEXT DEFAULT 'general',
                theater     TEXT DEFAULT 'global',
                status      TEXT DEFAULT 'not_observed'
                                CHECK(status IN
                                  ('not_observed','observed','denied','obsolete')),
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_iw_observations (
                id             TEXT PRIMARY KEY,
                indicator_id   TEXT NOT NULL,
                observed_status TEXT NOT NULL
                                    CHECK(observed_status IN
                                      ('observed','not_observed','denied')),
                source         TEXT,
                notes          TEXT,
                confidence     REAL DEFAULT 0.5,
                observed_at    TEXT NOT NULL,
                created_at     TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_iw_indicators_coa "
            "ON sg_iw_indicators(coa_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_iw_indicators_theater "
            "ON sg_iw_indicators(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_iw_observations_indicator "
            "ON sg_iw_observations(indicator_id)"
        )
        conn.commit()
        print("Migration 088 up: sg_iw_indicators + sg_iw_observations created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
