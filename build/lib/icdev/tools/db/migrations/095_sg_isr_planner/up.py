#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 095 — ISR Collection Planner."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_isr_requirements (
                id              TEXT PRIMARY KEY,
                nai             TEXT NOT NULL,
                collection_type TEXT DEFAULT 'IMINT'
                                    CHECK(collection_type IN
                                      ('IMINT','SIGINT','HUMINT','OSINT','MASINT','mixed')),
                priority        INTEGER DEFAULT 3,
                theater         TEXT DEFAULT 'unspecified',
                earliest        TEXT,
                latest          TEXT,
                purpose         TEXT,
                status          TEXT DEFAULT 'open'
                                    CHECK(status IN ('open','assigned','satisfied','cancelled')),
                assigned_asset_id TEXT,
                created_by      TEXT DEFAULT 'analyst',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_isr_assets (
                id              TEXT PRIMARY KEY,
                asset_name      TEXT NOT NULL,
                asset_type      TEXT DEFAULT 'UAV'
                                    CHECK(asset_type IN
                                      ('UAV','satellite','SIGINT_aircraft',
                                       'HUMINT_asset','surface','cyber')),
                theater         TEXT DEFAULT 'unspecified',
                collection_caps TEXT DEFAULT '[]',
                status          TEXT DEFAULT 'available'
                                    CHECK(status IN
                                      ('available','tasked','degraded','offline')),
                notes           TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_isr_req_theater "
            "ON sg_isr_requirements(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_isr_asset_theater "
            "ON sg_isr_assets(theater)"
        )
        conn.commit()
        print("Migration 095 up: sg_isr_requirements + sg_isr_assets created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
