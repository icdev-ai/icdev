-- CUI // SP-CTI
-- Migration 20260808161736: sag_standing_goals — durable standing goals for the
-- standalone-agent runtime (hgx-goal-01).
--
-- Backs tools/agent_runtime/standing_goals.py. One row per goal: a durable
-- objective that outlives a single session and is re-injected into the agent's
-- system prompt while it is `active`.
--
-- Conventions (mirror migrations 287/290/291): first-class tenant_id + user_id +
-- classification columns make it RLS-eligible via tools.db.storage
-- get_connection(); CREATE TABLE IF NOT EXISTS is idempotent and dialect-neutral
-- (PostgreSQL primary, SQLite init/test fallback). Structured fields are stored
-- as JSON TEXT and parsed in Python — never with json_extract/json_each, which
-- is SQLite-only dialect. NOT append-only: a goal is mutable lifecycle state
-- (activate / pause / progress / complete) and `delete` is a real DELETE.
--
-- Status is the GoalStatus vocabulary in the module
-- (pending/active/paused/blocked/completed/cancelled). Deliberately NOT a CHECK
-- constraint: the module owns the transition table, and a CHECK here would have
-- to be hand-kept in sync with the Python enum, which is exactly the drift the
-- "derive from Python constants" rule exists to prevent.
--
-- The runtime module also self-creates this via _ensure_schema(), so a checkout
-- that has not run this migration still works; every read/write degrades to an
-- empty result rather than raising when the table or the DB is unavailable.

CREATE TABLE IF NOT EXISTS sag_standing_goals (
    goal_id        TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    tenant_id      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    title          TEXT NOT NULL,
    detail         TEXT DEFAULT '',
    status         TEXT DEFAULT 'pending',
    priority       INTEGER DEFAULT 50,
    progress       INTEGER DEFAULT 0,
    context_id     TEXT DEFAULT '',
    session_id     TEXT DEFAULT '',
    tags_json      TEXT DEFAULT '[]',
    metadata_json  TEXT DEFAULT '{}',
    blocked_reason TEXT DEFAULT '',
    created_at     TEXT,
    updated_at     TEXT,
    activated_at   TEXT,
    completed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sag_standing_goals_owner_status
    ON sag_standing_goals (user_id, tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_sag_standing_goals_context
    ON sag_standing_goals (context_id);
