#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 203 — gitlab_inbox table for durable GitLab note receipt.

Provides a persistent local buffer for inbound GitLab note (comment) webhooks.
Notes containing !icdev commands are written here on receipt, then processed
idempotently. Rows are NOT append-only (they are updated when processed).

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MIGRATION_ID = "203"
MIGRATION_NAME = "gitlab_inbox"
DESCRIPTION = "Create gitlab_inbox table for durable GitLab note receipt and replay"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gitlab_inbox (
    note_id            INTEGER PRIMARY KEY,
    message_json       TEXT NOT NULL,
    issue_iid          INTEGER,
    author_username    TEXT,
    text               TEXT,
    processed_at       TEXT,
    error              TEXT,
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gitlab_inbox_processed_at
    ON gitlab_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_gitlab_inbox_created_at
    ON gitlab_inbox(created_at DESC);
"""


def up(conn=None) -> None:
    """Apply migration: create gitlab_inbox table."""
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
