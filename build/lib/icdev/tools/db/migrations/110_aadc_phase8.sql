-- CUI // SP-CTI
-- Migration 110: AADC Phase 8 — Design Intelligence & Analytics
-- Tables: aadc_pattern_reports, aadc_impact_reports

CREATE TABLE IF NOT EXISTS aadc_pattern_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    dominant_pattern TEXT DEFAULT 'UNCLASSIFIED',
    pattern_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_pattern_design ON aadc_pattern_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_impact_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    resilience_score REAL DEFAULT 100.0,
    spof_count      INTEGER DEFAULT 0,
    overall_risk_level TEXT DEFAULT 'LOW',
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_impact_design ON aadc_impact_reports(design_id);
