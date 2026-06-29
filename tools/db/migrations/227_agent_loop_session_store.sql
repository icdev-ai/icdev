-- CUI // SP-CTI
-- Migration 227: agent loop session checkpoints + parent_session_id tracing
--
-- Supports:
--   • Session resumption  — run_agent_loop(resume_session_id=...) loads
--     messages_json and replays from the last saved turn rather than restarting.
--   • Distributed tracing — parent_session_id links a child agent session
--     to the parent that spawned it (e.g. parallel_agents fan-out).
--
-- The ON CONFLICT upsert is handled in Python (session_store.py) using
-- psycopg2 for PostgreSQL.  The schema itself is compatible with both PG
-- and SQLite (TEXT/INTEGER/REAL types, no PG-only constructs).

CREATE TABLE IF NOT EXISTS agent_loop_checkpoints (
    session_id        TEXT    PRIMARY KEY,
    parent_session_id TEXT    NOT NULL DEFAULT '',
    turn_number       INTEGER NOT NULL DEFAULT 0,
    messages_json     TEXT    NOT NULL DEFAULT '[]',
    model_id          TEXT    NOT NULL DEFAULT '',
    provider          TEXT    NOT NULL DEFAULT '',
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0.0,
    updated_at        TEXT    NOT NULL DEFAULT ''
);

-- Fast lookup of child sessions given a parent session ID.
CREATE INDEX IF NOT EXISTS idx_alc_parent_session
    ON agent_loop_checkpoints (parent_session_id)
    WHERE parent_session_id != '';

-- Most-recent-first ordering for the ops list endpoint.
CREATE INDEX IF NOT EXISTS idx_alc_updated_at
    ON agent_loop_checkpoints (updated_at DESC);
