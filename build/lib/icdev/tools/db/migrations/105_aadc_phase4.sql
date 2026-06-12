-- CUI // SP-CTI
-- Migration 105: AADC Phase 4 — checkpoint/fork tables, parallel groups
-- Adds: aadc_checkpoints, aadc_parallel_groups

CREATE TABLE IF NOT EXISTS aadc_checkpoints (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES aadc_designs(id) ON DELETE CASCADE,
    node_id         TEXT DEFAULT '',        -- associated checkpoint node id (optional)
    label           TEXT DEFAULT '',
    graph_json      TEXT NOT NULL,          -- full graph snapshot at this checkpoint
    created_by      TEXT DEFAULT 'user',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aadc_checkpoints_design
    ON aadc_checkpoints(design_id);

CREATE TABLE IF NOT EXISTS aadc_parallel_groups (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES aadc_designs(id) ON DELETE CASCADE,
    label           TEXT DEFAULT 'Parallel Group',
    color           TEXT DEFAULT '#7e22ce',
    node_ids_json   TEXT NOT NULL DEFAULT '[]',  -- JSON array of node IDs in group
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aadc_parallel_groups_design
    ON aadc_parallel_groups(design_id);
