"""Migration — create sdc_compliance_timeline + sdc_roi_metrics tables.

Phase 2 Synthetic Data tables for the Security Design Canvas. Compliance
timeline snapshots track posture grades and remediation effort over time;
ROI metrics capture manual vs automated hours and multiplier calculations.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS sdc_compliance_timeline (
    id                      TEXT PRIMARY KEY,
    design_id               TEXT NOT NULL,
    snapshot_label          TEXT NOT NULL DEFAULT '',
    cat1_count              INTEGER NOT NULL DEFAULT 0,
    cat2_count              INTEGER NOT NULL DEFAULT 0,
    cat3_count              INTEGER NOT NULL DEFAULT 0,
    risk_score              REAL NOT NULL DEFAULT 0.0,
    posture_grade           TEXT NOT NULL DEFAULT 'F',
    controls_implemented    INTEGER NOT NULL DEFAULT 0,
    remediation_hours       REAL NOT NULL DEFAULT 0.0,
    snapshot_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sdc_compliance_timeline_design
    ON sdc_compliance_timeline (design_id, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS sdc_roi_metrics (
    id                TEXT PRIMARY KEY,
    design_id         TEXT NOT NULL,
    manual_hours      REAL NOT NULL DEFAULT 0.0,
    automated_hours   REAL NOT NULL DEFAULT 0.0,
    cost_per_hour     REAL NOT NULL DEFAULT 0.0,
    roi_multiplier    REAL NOT NULL DEFAULT 1.0,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sdc_roi_metrics_design
    ON sdc_roi_metrics (design_id, created_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
