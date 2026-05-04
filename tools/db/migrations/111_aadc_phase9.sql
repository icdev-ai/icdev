-- CUI // SP-CTI
-- Migration 111: AADC Phase 9 — Unified Scorecard, Deployment Gate & Findings Inbox
-- Tables: aadc_scorecard_snapshots, aadc_deploy_gates

CREATE TABLE IF NOT EXISTS aadc_scorecard_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    overall_score   REAL DEFAULT 0.0,
    health          TEXT DEFAULT 'UNRATED',
    snapshot_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_scorecard_design ON aadc_scorecard_snapshots(design_id);

CREATE TABLE IF NOT EXISTS aadc_deploy_gates (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    verdict         TEXT DEFAULT 'BLOCKED',
    blocker_count   INTEGER DEFAULT 0,
    warning_count   INTEGER DEFAULT 0,
    gate_json       TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_deploy_gate_design ON aadc_deploy_gates(design_id);
