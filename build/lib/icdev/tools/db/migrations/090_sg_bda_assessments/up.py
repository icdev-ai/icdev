#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 090 — Battle Damage Assessment (BDA) table.

Stores post-strike effects assessments. Append-only (NIST AU).
"""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_bda_assessments (
                id              TEXT PRIMARY KEY,
                target_id       TEXT,
                target_name     TEXT NOT NULL,
                strike_time     TEXT,
                method          TEXT DEFAULT 'unknown'
                                    CHECK(method IN
                                      ('kinetic','cyber','ew','unknown')),
                physical_damage TEXT DEFAULT 'unknown'
                                    CHECK(physical_damage IN
                                      ('destroyed','severely_damaged',
                                       'moderately_damaged','negligible','unknown')),
                functional_defeat INTEGER DEFAULT 0,
                confidence      REAL DEFAULT 0.5,
                collection_method TEXT DEFAULT 'IMINT'
                                      CHECK(collection_method IN
                                        ('IMINT','SIGINT','HUMINT','OSINT',
                                         'SOCMINT','combined','unknown')),
                analyst_notes   TEXT,
                theater         TEXT DEFAULT 'unspecified',
                classification  TEXT DEFAULT 'CUI // SP-CTI',
                created_by      TEXT DEFAULT 'analyst',
                created_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_bda_created "
            "ON sg_bda_assessments(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_bda_target "
            "ON sg_bda_assessments(target_id)"
        )
        conn.commit()
        print("Migration 090 up: sg_bda_assessments created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
