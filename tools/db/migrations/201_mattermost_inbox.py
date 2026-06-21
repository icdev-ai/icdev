#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 201 — mattermost_inbox table for durable Mattermost post receipt.

Provides a persistent local buffer for inbound Mattermost outgoing webhook posts.
Posts are written here on receipt, then processed idempotently.
Rows are NOT append-only (they are updated when processed).

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MIGRATION_ID = "201"
MIGRATION_NAME = "mattermost_inbox"
DESCRIPTION = "Create mattermost_inbox table for durable Mattermost post receipt and replay"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mattermost_inbox (
    post_id        TEXT PRIMARY KEY,
    message_json   TEXT NOT NULL,
    channel_id     TEXT,
    user_id        TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mattermost_inbox_processed_at
    ON mattermost_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_mattermost_inbox_created_at
    ON mattermost_inbox(created_at DESC);
"""


def up(conn=None) -> None:
    """Apply migration: create mattermost_inbox table."""
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
