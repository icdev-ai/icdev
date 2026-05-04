-- CUI // SP-CTI
-- Migration 106: AADC Phase 3 — safety redundancy + agent simulation tables

CREATE TABLE IF NOT EXISTS aadc_safety_graphs (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    score REAL DEFAULT 0.0,
    protected_count INTEGER DEFAULT 0,
    unprotected_count INTEGER DEFAULT 0,
    analysis_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aadc_safety_graphs_design ON aadc_safety_graphs(design_id);

CREATE TABLE IF NOT EXISTS aadc_agent_simulations (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    start_node_id TEXT NOT NULL DEFAULT '',
    input_payload TEXT DEFAULT '{}',
    trace_json TEXT NOT NULL DEFAULT '[]',
    decisions_json TEXT NOT NULL DEFAULT '[]',
    status TEXT DEFAULT 'complete',
    steps_count INTEGER DEFAULT 0,
    halted_by TEXT DEFAULT '',
    halted_by_label TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aadc_agent_simulations_design ON aadc_agent_simulations(design_id);
