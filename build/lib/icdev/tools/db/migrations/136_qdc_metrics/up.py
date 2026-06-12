# CUI // SP-CTI
"""Migration 136 — create qdc_metrics table.

Canonical schema registration for the qdc_metrics key/value store.
The table is read by tools/qdc/quality_scanner.py:scan_quality() to load
per-project quality metrics (code_coverage_pct, complexity_score, etc.)
that override built-in defaults when computing UQS scores.
"""

SQL = """
CREATE TABLE IF NOT EXISTS qdc_metrics (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    metric_key  TEXT NOT NULL,
    metric_value TEXT NOT NULL,
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_qdc_metrics_project
    ON qdc_metrics (project_id);

CREATE INDEX IF NOT EXISTS idx_qdc_metrics_key
    ON qdc_metrics (project_id, metric_key);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
