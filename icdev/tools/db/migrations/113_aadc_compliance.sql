-- CUI // SP-CTI
-- Migration 113: AADC Compliance — persistent compliance check state per project
-- Referenced by tools/aadc/compliance_checker.py

CREATE TABLE IF NOT EXISTS aadc_compliance (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    check_key       TEXT NOT NULL,
    check_value     TEXT NOT NULL DEFAULT '0',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_compliance_project ON aadc_compliance(project_id);
CREATE INDEX IF NOT EXISTS idx_aadc_compliance_key ON aadc_compliance(project_id, check_key);
