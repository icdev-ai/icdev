-- Migration 221: Agent loop mid-turn HITL pending requests
-- Tracks tool-call checkpoints paused for human approval within an agent loop run.
-- Canvas-scoped (ACE canvas DB, no classification/tenant_id — uses get_canvas_connection).
CREATE TABLE IF NOT EXISTS agent_hitl_pending (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL DEFAULT '',
    instance_id     TEXT NOT NULL DEFAULT '',
    coworker_id     TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL DEFAULT '',
    tool_input_json TEXT NOT NULL DEFAULT '{}',
    detail          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'timed_out')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ahp_instance ON agent_hitl_pending (instance_id);
CREATE INDEX IF NOT EXISTS idx_ahp_coworker ON agent_hitl_pending (coworker_id);
CREATE INDEX IF NOT EXISTS idx_ahp_status   ON agent_hitl_pending (status);
