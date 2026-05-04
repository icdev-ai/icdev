#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 074 — sg_opencti_indicators (OpenCTI threat intelligence indicators)."""

from tools.db.storage import get_connection

MIGRATION_ID = "074"
MIGRATION_NAME = "sg_opencti_indicators"
DESCRIPTION = "OpenCTI threat intelligence indicators for Strategos SGX"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_opencti_indicators (
        id             TEXT PRIMARY KEY,
        opencti_id     TEXT UNIQUE NOT NULL,
        name           TEXT NOT NULL,
        indicator_type TEXT,
        pattern        TEXT,
        confidence     INTEGER DEFAULT 0,
        tlp            TEXT DEFAULT 'TLP:WHITE',
        valid_from     TEXT,
        valid_until    TEXT,
        status         TEXT DEFAULT 'active'
            CHECK (status IN ('active','expired','revoked')),
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sgoi_opencti_id      ON sg_opencti_indicators(opencti_id)",
    "CREATE INDEX IF NOT EXISTS idx_sgoi_indicator_type  ON sg_opencti_indicators(indicator_type)",
    "CREATE INDEX IF NOT EXISTS idx_sgoi_status          ON sg_opencti_indicators(status)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[074_sg_opencti_indicators] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
