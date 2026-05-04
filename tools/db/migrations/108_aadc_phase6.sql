-- CUI // SP-CTI
-- Migration 108: AADC Phase 6 — ATO Readiness + Regulatory Gap tables
-- Safe to re-run (IF NOT EXISTS guards).

CREATE TABLE IF NOT EXISTS aadc_ato_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    score_pct       REAL DEFAULT 0.0,
    ato_ready       INTEGER DEFAULT 0,
    passed          INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    critical_failed INTEGER DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_ato_reports_design ON aadc_ato_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_regulatory_gaps (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    score_pct       REAL DEFAULT 0.0,
    compliant       INTEGER DEFAULT 0,
    gaps            INTEGER DEFAULT 0,
    critical_gaps   INTEGER DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_regulatory_gaps_design ON aadc_regulatory_gaps(design_id);
