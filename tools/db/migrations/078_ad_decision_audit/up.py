#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 078 — ad_decision_audit (SEC Rule 17a-4 immutable decision audit table)."""

from tools.db.storage import get_connection

MIGRATION_ID = "078"
MIGRATION_NAME = "ad_decision_audit"
DESCRIPTION = "Append-only decision audit table for FathomDesk analyst panel (SEC Rule 17a-4 / NIST AU)"

_DDL = [
    """CREATE TABLE IF NOT EXISTS ad_decision_audit (
        id                  TEXT PRIMARY KEY,
        ticker              TEXT NOT NULL,
        as_of_date          TEXT NOT NULL,
        fundamentals_score  REAL,
        technical_score     REAL,
        sentiment_score     REAL,
        macro_score         REAL,
        bull_confidence     REAL,
        bear_confidence     REAL,
        final_direction     TEXT,
        final_confidence    REAL,
        reasoning           TEXT,
        venue               TEXT,
        instrument_type     TEXT,
        mifid_timestamp     TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ad_decision_audit_ticker   ON ad_decision_audit(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_ad_decision_audit_date     ON ad_decision_audit(as_of_date)",
    "CREATE INDEX IF NOT EXISTS idx_ad_decision_audit_dir      ON ad_decision_audit(final_direction)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[078_ad_decision_audit] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
