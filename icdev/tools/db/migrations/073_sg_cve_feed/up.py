#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 073 — sg_cve_feed (CVE vulnerability feed for Strategos SGX)."""

from tools.db.storage import get_connection

MIGRATION_ID = "073"
MIGRATION_NAME = "sg_cve_feed"
DESCRIPTION = "CVE vulnerability feed entries for cyber threat situational awareness"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_cve_feed (
        id           TEXT PRIMARY KEY,
        cve_id       TEXT UNIQUE NOT NULL,
        description  TEXT,
        cvss_score   REAL DEFAULT 0.0,
        cvss_vector  TEXT,
        severity     TEXT DEFAULT 'unknown'
            CHECK (severity IN ('critical','high','medium','low','unknown')),
        vendor       TEXT,
        product      TEXT,
        published_at TEXT NOT NULL,
        modified_at  TEXT NOT NULL,
        status       TEXT DEFAULT 'active'
            CHECK (status IN ('active','patched','disputed','rejected'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sgcf_cve_id    ON sg_cve_feed(cve_id)",
    "CREATE INDEX IF NOT EXISTS idx_sgcf_severity  ON sg_cve_feed(severity)",
    "CREATE INDEX IF NOT EXISTS idx_sgcf_published ON sg_cve_feed(published_at)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[073_sg_cve_feed] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
