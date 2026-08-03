-- CUI // SP-CTI
-- Migration 200 — teams_inbox table for durable Teams message receipt.
--
-- Provides a persistent local buffer for inbound Teams Bot Framework messages.
-- Messages are written here on webhook receipt, then processed idempotently by
-- tools/notifications/adapters/teams_listener.py. Rows are NOT append-only
-- (processed_at and error are updated once a row is handled).
--
-- Was 200_teams_inbox.py, a bare .py file the runner never discovered, so the
-- table was never created on any database that did not bootstrap from
-- pg_consolidated.sql (mvs-invisible-04). Idempotent: IF NOT EXISTS throughout.

-- @pg-only
CREATE TABLE IF NOT EXISTS teams_inbox (
    message_id     TEXT PRIMARY KEY,
    message_json   TEXT NOT NULL,
    channel_id     TEXT,
    sender_id      TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT NOW()
);

-- @sqlite-only
CREATE TABLE IF NOT EXISTS teams_inbox (
    message_id     TEXT PRIMARY KEY,
    message_json   TEXT NOT NULL,
    channel_id     TEXT,
    sender_id      TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

-- @all
CREATE INDEX IF NOT EXISTS idx_teams_inbox_processed_at
    ON teams_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_teams_inbox_created_at
    ON teams_inbox(created_at DESC);
