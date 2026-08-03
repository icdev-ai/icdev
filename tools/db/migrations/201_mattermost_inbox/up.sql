-- CUI // SP-CTI
-- Migration 201 — mattermost_inbox table for durable MatterMost post receipt.
--
-- Persistent local buffer for inbound MatterMost posts, polled and processed by
-- tools/notifications/adapters/mattermost_listener.py. Rows are NOT append-only
-- (processed_at and error are updated once a row is handled).
--
-- Was 201_mattermost_inbox.py, a bare .py file the runner never discovered, so
-- the table was never created on any database that did not bootstrap from
-- pg_consolidated.sql (mvs-invisible-04). Idempotent: IF NOT EXISTS throughout.

-- @pg-only
CREATE TABLE IF NOT EXISTS mattermost_inbox (
    post_id        TEXT PRIMARY KEY,
    message_json   TEXT NOT NULL,
    channel_id     TEXT,
    user_id        TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT NOW()
);

-- @sqlite-only
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

-- @all
CREATE INDEX IF NOT EXISTS idx_mattermost_inbox_processed_at
    ON mattermost_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_mattermost_inbox_created_at
    ON mattermost_inbox(created_at DESC);
