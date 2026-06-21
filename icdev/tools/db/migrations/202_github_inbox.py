#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 202 — github_inbox table for durable GitHub comment receipt.

Provides a persistent local buffer for inbound GitHub issue/PR comment webhooks.
Comments containing !icdev commands are written here on receipt, then processed
idempotently. Rows are NOT append-only (they are updated when processed).

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MIGRATION_ID = "202"
MIGRATION_NAME = "github_inbox"
DESCRIPTION = "Create github_inbox table for durable GitHub comment receipt and replay"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS github_inbox (
    comment_id     INTEGER PRIMARY KEY,
    message_json   TEXT NOT NULL,
    issue_number   INTEGER,
    user_login     TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_github_inbox_processed_at
    ON github_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_github_inbox_created_at
    ON github_inbox(created_at DESC);
"""


def up(conn=None) -> None:
    """Apply migration: create github_inbox table."""
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
