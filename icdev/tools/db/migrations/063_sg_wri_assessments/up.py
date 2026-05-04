#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 063 — sg_wri_assessments (PMESII-PT weighted risk index)."""

from tools.db.storage import get_connection

_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS sg_wri_assessments (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        wri                  REAL NOT NULL,
        escalation_rung      INTEGER NOT NULL CHECK(escalation_rung BETWEEN 1 AND 5),
        political            REAL NOT NULL DEFAULT 0,
        military             REAL NOT NULL DEFAULT 0,
        economic             REAL NOT NULL DEFAULT 0,
        social               REAL NOT NULL DEFAULT 0,
        information          REAL NOT NULL DEFAULT 0,
        infrastructure       REAL NOT NULL DEFAULT 0,
        physical_environment REAL NOT NULL DEFAULT 0,
        time                 REAL NOT NULL DEFAULT 0,
        dominant_indicators  TEXT,
        source               TEXT,
        created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_wri_created ON sg_wri_assessments(created_at)",
]


def up(conn=None):
    conn = get_connection()
    try:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        print("[063_sg_wri_assessments] migration up complete")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
