-- CUI // SP-CTI
-- Migration 107: AADC Phase 5 — risk register + threat model tables

CREATE TABLE IF NOT EXISTS aadc_risk_items (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    risk_category TEXT DEFAULT 'operational',
    severity TEXT DEFAULT 'MEDIUM',
    likelihood TEXT DEFAULT 'MEDIUM',
    impact TEXT DEFAULT 'MEDIUM',
    status TEXT DEFAULT 'open',
    owner TEXT DEFAULT '',
    mitigation TEXT DEFAULT '',
    finding_id TEXT DEFAULT '',
    node_id TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aadc_risk_items_design ON aadc_risk_items(design_id);
CREATE INDEX IF NOT EXISTS idx_aadc_risk_items_status ON aadc_risk_items(status);

CREATE TABLE IF NOT EXISTS aadc_threat_models (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    stride_json TEXT NOT NULL DEFAULT '[]',
    atlas_threats TEXT NOT NULL DEFAULT '[]',
    threat_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aadc_threat_models_design ON aadc_threat_models(design_id);
