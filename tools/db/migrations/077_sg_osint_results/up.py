#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 077 — sg_osint_results (unified OSINT findings output table)."""

from tools.db.storage import get_connection

MIGRATION_ID = "077"
MIGRATION_NAME = "sg_osint_results"
DESCRIPTION = "Unified OSINT findings table fed by EO, SIGINT, and SOCMINT collection pipelines"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_osint_results (
        id           TEXT PRIMARY KEY,
        scan_id      TEXT NOT NULL,
        target       TEXT,
        source       TEXT,
        finding_type TEXT,
        title        TEXT,
        data_json    TEXT,
        status       TEXT NOT NULL DEFAULT 'new',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sg_osint_scan        ON sg_osint_results(scan_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_osint_type        ON sg_osint_results(finding_type)",
    "CREATE INDEX IF NOT EXISTS idx_sg_osint_status      ON sg_osint_results(status)",
    "CREATE INDEX IF NOT EXISTS idx_sg_osint_target      ON sg_osint_results(target)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[077_sg_osint_results] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
