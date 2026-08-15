#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 021 — sg_sigint_events table for Strategos SIGINT beacon detection.

Stores raw SIGINT detection events emitted by the SDC/ODC sensor pipeline.
Rows are append-only (NIST AU); never UPDATE or DELETE.

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "021"
MIGRATION_NAME = "sg_sigint_events"
DESCRIPTION = "Create sg_sigint_events table for Strategos SIGINT beacon detection records"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_sigint_events (
    id                TEXT PRIMARY KEY,
    beacon_hash       TEXT,
    freq_mhz          REAL,
    signal_type       TEXT,
    attribution_score REAL,
    lat               REAL,
    lon               REAL,
    raw_iq_ref        TEXT,
    detected_at       TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sg_sigint_events_detected_at
    ON sg_sigint_events(detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_sg_sigint_events_beacon_hash
    ON sg_sigint_events(beacon_hash);
"""


def up(conn=None) -> None:
    """Apply migration: create sg_sigint_events table in icdev.db."""
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
