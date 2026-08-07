-- CUI // SP-CTI
-- Migration 204 — skype_inbox table for durable Skype Activity receipt.
--
-- Skype has no polling API: every inbound Activity arrives via webhook and is
-- stored here by the gateway adapter for asynchronous processing, then read by
-- tools/databridge/connectors/skype_connector.py. service_url holds the Bot
-- Framework reply endpoint from the inbound Activity, required to reply. Rows
-- are NOT append-only (processed_at and error are updated once handled).
--
-- Was 204_skype_inbox.py, a bare .py file the runner never discovered, so the
-- table was never created on any database that did not bootstrap from
-- pg_consolidated.sql (mvs-invisible-04). Idempotent: IF NOT EXISTS throughout.

-- @pg-only
CREATE TABLE IF NOT EXISTS skype_inbox (
    activity_id      TEXT PRIMARY KEY,
    message_json     TEXT NOT NULL,
    conversation_id  TEXT,
    service_url      TEXT,
    sender_id        TEXT,
    text             TEXT,
    processed_at     TEXT,
    error            TEXT,
    created_at       TEXT DEFAULT NOW()
);

-- @sqlite-only
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

-- @all
CREATE INDEX IF NOT EXISTS idx_skype_inbox_processed_at
    ON skype_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_skype_inbox_created_at
    ON skype_inbox(created_at DESC);
