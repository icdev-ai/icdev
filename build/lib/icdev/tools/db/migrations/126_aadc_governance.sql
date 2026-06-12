-- CUI // SP-CTI
-- Migration 126: AADC Governance — persistent AI governance posture per project
-- Referenced by tools/aadc/governance_scanner.py

CREATE TABLE IF NOT EXISTS aadc_governance (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    check_key       TEXT NOT NULL,
    check_value     TEXT NOT NULL DEFAULT '0',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_governance_project ON aadc_governance(project_id);
CREATE INDEX IF NOT EXISTS idx_aadc_governance_key ON aadc_governance(project_id, check_key);
