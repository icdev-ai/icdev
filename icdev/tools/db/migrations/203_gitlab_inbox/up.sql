-- CUI // SP-CTI
-- Migration 203 — gitlab_inbox table for durable GitLab issue-note receipt.
--
-- Persistent local buffer for inbound GitLab issue notes, polled for the
-- "!icdev" trigger by tools/notifications/adapters/gitlab_listener.py. Rows are
-- NOT append-only (processed_at and error are updated once a row is handled).
--
-- Was 203_gitlab_inbox.py, a bare .py file the runner never discovered, so the
-- table was never created on any database that did not bootstrap from
-- pg_consolidated.sql (mvs-invisible-04). Idempotent: IF NOT EXISTS throughout.

-- @pg-only
CREATE TABLE IF NOT EXISTS gitlab_inbox (
    note_id            INTEGER PRIMARY KEY,
    message_json       TEXT NOT NULL,
    issue_iid          INTEGER,
    author_username    TEXT,
    text               TEXT,
    processed_at       TEXT,
    error              TEXT,
    created_at         TEXT DEFAULT NOW()
);

-- @sqlite-only
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

-- @all
CREATE INDEX IF NOT EXISTS idx_gitlab_inbox_processed_at
    ON gitlab_inbox(processed_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_gitlab_inbox_created_at
    ON gitlab_inbox(created_at DESC);
