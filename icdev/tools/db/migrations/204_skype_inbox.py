#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 204 — skype_inbox table for durable Skype Activity receipt.

Provides a persistent local buffer for inbound Skype Bot Framework Activities.
Skype has no polling API — all inbound messages arrive via webhook and are stored
here by the gateway adapter for asynchronous processing.
Rows are NOT append-only (they are updated when processed).

The service_url column stores the Bot Framework reply endpoint from the inbound
Activity, required to send replies back to Skype.

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MIGRATION_ID = "204"
MIGRATION_NAME = "skype_inbox"
DESCRIPTION = "Create skype_inbox table for durable Skype Bot Framework Activity receipt"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skype_inbox (
    activity_id      TEXT PRIMARY KEY,
    message_json     TEXT NOT NULL,
    conversation_id  TEXT,
    service_url      TEXT,
    sender_id        TEXT,
    text             TEXT,
    processed_at     TEXT,
    error            TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skype_inbox_processed_at
    ON skype_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_skype_inbox_created_at
    ON skype_inbox(created_at DESC);
"""


def up(conn=None) -> None:
    """Apply migration: create skype_inbox table."""
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
