-- CUI // SP-CTI
-- Migration 127: qdc_metrics — persistent quality design metrics per project
-- Referenced by tools/qdc/quality_scanner.py
-- Resolves orphan_db_table gap detected by the Internal Awareness Engine.

CREATE TABLE IF NOT EXISTS qdc_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL,
    metric_key   TEXT NOT NULL,
    metric_value REAL,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_qdc_metrics_project ON qdc_metrics(project_id);
CREATE INDEX IF NOT EXISTS idx_qdc_metrics_project_key ON qdc_metrics(project_id, metric_key);
