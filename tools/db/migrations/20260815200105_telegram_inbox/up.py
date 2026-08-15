#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 024 — telegram_inbox table for durable Telegram message receipt.

Provides a persistent local buffer between Telegram getUpdates and Kanban task
creation. Messages are inserted here first, then processed idempotently.
Rows are NOT append-only (they are updated when processed).

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MIGRATION_ID = "024"
MIGRATION_NAME = "telegram_inbox"
DESCRIPTION = "Create telegram_inbox table for durable Telegram message receipt and replay"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_inbox (
    update_id      INTEGER PRIMARY KEY,
    message_json   TEXT NOT NULL,
    chat_id        TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_telegram_inbox_processed_at
    ON telegram_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_telegram_inbox_created_at
    ON telegram_inbox(created_at DESC);
"""


def up(conn=None) -> None:
    """Apply migration: create telegram_inbox table in icdev.db."""
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
