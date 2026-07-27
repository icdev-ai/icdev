-- Migration 220: Agent loop session persistence
-- Stores AgentLoopResult message history for resume across process restarts.
-- Canvas-scoped table (no classification/tenant_id — uses get_canvas_connection).
CREATE TABLE IF NOT EXISTS agent_loop_sessions (
    session_id        TEXT PRIMARY KEY,
    instance_id       TEXT NOT NULL DEFAULT '',
    coworker_id       TEXT NOT NULL DEFAULT '',
    llm_function      TEXT NOT NULL DEFAULT '',
    result_subtype    TEXT NOT NULL DEFAULT '',
    turns             INTEGER NOT NULL DEFAULT 0,
    total_input_tokens  INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd    REAL NOT NULL DEFAULT 0.0,
    messages_json     TEXT NOT NULL DEFAULT '[]',
    system_prompt     TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_als_instance ON agent_loop_sessions (instance_id);
CREATE INDEX IF NOT EXISTS idx_als_coworker ON agent_loop_sessions (coworker_id);
