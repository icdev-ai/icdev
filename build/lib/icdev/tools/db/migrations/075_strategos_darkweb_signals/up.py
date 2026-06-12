#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 075 — strategos_darkweb_signals (dark web intelligence signal feed)."""

from tools.db.storage import get_connection

MIGRATION_ID = "075"
MIGRATION_NAME = "strategos_darkweb_signals"
DESCRIPTION = "Dark web intelligence signals for Strategos dark web monitor"

_DDL = [
    """CREATE TABLE IF NOT EXISTS strategos_darkweb_signals (
        id           TEXT PRIMARY KEY,
        score        REAL NOT NULL DEFAULT 0.0,
        signal_type  TEXT NOT NULL
            CHECK (signal_type IN (
                'leaked_creds','threat_actor','exploit_sale','data_dump',
                'marketplace','forum_post','ransomware','api_exposure'
            )),
        title        TEXT NOT NULL,
        source       TEXT,
        collected_at TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'new'
            CHECK (status IN ('new','bridged','reviewed')),
        classification TEXT DEFAULT 'CUI // SP-CTI'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sdws_status       ON strategos_darkweb_signals(status)",
    "CREATE INDEX IF NOT EXISTS idx_sdws_signal_type  ON strategos_darkweb_signals(signal_type)",
    "CREATE INDEX IF NOT EXISTS idx_sdws_score        ON strategos_darkweb_signals(score DESC)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[075_strategos_darkweb_signals] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
