-- CUI // SP-CTI
-- 176_capture_phase_gates.sql
-- Adds Shipley phase-gate lifecycle tracking to pg_capture_plans (prop-cap-11)
ALTER TABLE pg_capture_plans ADD COLUMN current_phase TEXT DEFAULT 'qualify';

CREATE TABLE IF NOT EXISTS pg_capture_gate_decisions (
    id TEXT PRIMARY KEY,
    capture_plan_id TEXT NOT NULL,
    opportunity_id TEXT,
    from_phase TEXT NOT NULL,
    to_phase TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT,
    decided_by TEXT,
    gate_criteria_met TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pg_cap_gates_plan ON pg_capture_gate_decisions(capture_plan_id);
CREATE INDEX IF NOT EXISTS idx_pg_cap_gates_created ON pg_capture_gate_decisions(created_at);
