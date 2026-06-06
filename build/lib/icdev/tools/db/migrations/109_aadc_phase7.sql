-- CUI // SP-CTI
-- Migration 109: AADC Phase 7 — Red Team + Lint report tables
-- Safe to re-run (IF NOT EXISTS guards).

CREATE TABLE IF NOT EXISTS aadc_red_team_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    overall_risk    TEXT DEFAULT 'UNKNOWN',
    applicable      INTEGER DEFAULT 0,
    unmitigated     INTEGER DEFAULT 0,
    critical_unmitigated INTEGER DEFAULT 0,
    avg_exploitability REAL DEFAULT 0.0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_red_team_design ON aadc_red_team_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_lint_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    lint_score      REAL DEFAULT 100.0,
    total_issues    INTEGER DEFAULT 0,
    critical_issues INTEGER DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_lint_design ON aadc_lint_reports(design_id);
