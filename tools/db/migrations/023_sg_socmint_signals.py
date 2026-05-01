#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 023 — sg_socmint_signals table for Strategos SOCMINT signal ingestion.

Stores social media intelligence signals ingested by the SOCMINT pipeline.
Rows are append-only (NIST AU); never UPDATE or DELETE.

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "023"
MIGRATION_NAME = "sg_socmint_signals"
DESCRIPTION = "Create sg_socmint_signals table for Strategos SOCMINT social media signal records"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_socmint_signals (
    id                TEXT PRIMARY KEY,
    platform          TEXT,
    channel_id        TEXT,
    message_id        TEXT UNIQUE,
    text              TEXT,
    media_url         TEXT,
    posted_at         TEXT,
    relevance_score   REAL,
    geo_hint          TEXT,
    status            TEXT DEFAULT 'new',
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sg_socmint_signals_posted_at
    ON sg_socmint_signals(posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_sg_socmint_signals_platform
    ON sg_socmint_signals(platform);

CREATE INDEX IF NOT EXISTS idx_sg_socmint_signals_channel_id
    ON sg_socmint_signals(channel_id);

CREATE INDEX IF NOT EXISTS idx_sg_socmint_signals_status
    ON sg_socmint_signals(status);
"""


def up(conn=None) -> None:
    """Apply migration: create sg_socmint_signals table in icdev.db."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        _close = True
    else:
        _close = False

    conn.executescript(_SCHEMA)
    conn.commit()

    if _close:
        conn.close()

    print(f"[migration {MIGRATION_ID}] {DESCRIPTION} — applied")


if __name__ == "__main__":
    up()
