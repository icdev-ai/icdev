-- CUI // SP-CTI
-- Migration 202 — github_inbox table for durable GitHub issue-comment receipt.
--
-- Persistent local buffer for inbound GitHub issue comments, polled for the
-- "!icdev" trigger by tools/notifications/adapters/github_listener.py. Rows are
-- NOT append-only (processed_at and error are updated once a row is handled).
--
-- Was 202_github_inbox.py, a bare .py file the runner never discovered, so the
-- table was never created on any database that did not bootstrap from
-- pg_consolidated.sql (mvs-invisible-04). Idempotent: IF NOT EXISTS throughout.

-- @pg-only
CREATE TABLE IF NOT EXISTS github_inbox (
    comment_id     INTEGER PRIMARY KEY,
    message_json   TEXT NOT NULL,
    issue_number   INTEGER,
    user_login     TEXT,
    text           TEXT,
    processed_at   TEXT,
    error          TEXT,
    created_at     TEXT DEFAULT NOW()
);

-- @sqlite-only
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

-- @all
CREATE INDEX IF NOT EXISTS idx_github_inbox_processed_at
    ON github_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_github_inbox_created_at
    ON github_inbox(created_at DESC);
