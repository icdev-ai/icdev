# CUI // SP-CTI
"""Migration 161 — Create sdc_compliance_timeline and sdc_roi_metrics tables.

sdc_compliance_timeline: before/after compliance snapshots per security design,
used by the SDC demo compliance timeline widget and ROI calculator.

sdc_roi_metrics: per-design ROI data (manual vs automated hours/cost),
queried by tools/sdc/roi_calculator.py.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sdc_compliance_timeline (
                id                    TEXT PRIMARY KEY,
                design_id             TEXT NOT NULL,
                snapshot_label        TEXT NOT NULL DEFAULT 'baseline',
                cat1_count            INTEGER NOT NULL DEFAULT 0,
                cat2_count            INTEGER NOT NULL DEFAULT 0,
                cat3_count            INTEGER NOT NULL DEFAULT 0,
                risk_score            REAL NOT NULL DEFAULT 0.0,
                posture_grade         TEXT NOT NULL DEFAULT 'F',
                controls_implemented  INTEGER NOT NULL DEFAULT 0,
                controls_total        INTEGER NOT NULL DEFAULT 0,
                remediation_hours     REAL NOT NULL DEFAULT 0.0,
                snapshot_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                classification        TEXT NOT NULL DEFAULT 'CUI'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sdc_roi_metrics (
                id               TEXT PRIMARY KEY,
                design_id        TEXT NOT NULL,
                manual_hours     REAL NOT NULL DEFAULT 200.0,
                automated_hours  REAL NOT NULL DEFAULT 4.0,
                cost_per_hour    REAL NOT NULL DEFAULT 150.0,
                roi_multiplier   REAL NOT NULL DEFAULT 0.0,
                engagement_type  TEXT NOT NULL DEFAULT 'standard',
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                classification   TEXT NOT NULL DEFAULT 'CUI'
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sdc_compliance_timeline (
                id                    TEXT PRIMARY KEY,
                design_id             TEXT NOT NULL,
                snapshot_label        TEXT NOT NULL DEFAULT 'baseline',
                cat1_count            INTEGER NOT NULL DEFAULT 0,
                cat2_count            INTEGER NOT NULL DEFAULT 0,
                cat3_count            INTEGER NOT NULL DEFAULT 0,
                risk_score            REAL NOT NULL DEFAULT 0.0,
                posture_grade         TEXT NOT NULL DEFAULT 'F',
                controls_implemented  INTEGER NOT NULL DEFAULT 0,
                controls_total        INTEGER NOT NULL DEFAULT 0,
                remediation_hours     REAL NOT NULL DEFAULT 0.0,
                snapshot_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                classification        TEXT NOT NULL DEFAULT 'CUI'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sdc_roi_metrics (
                id               TEXT PRIMARY KEY,
                design_id        TEXT NOT NULL,
                manual_hours     REAL NOT NULL DEFAULT 200.0,
                automated_hours  REAL NOT NULL DEFAULT 4.0,
                cost_per_hour    REAL NOT NULL DEFAULT 150.0,
                roi_multiplier   REAL NOT NULL DEFAULT 0.0,
                engagement_type  TEXT NOT NULL DEFAULT 'standard',
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                classification   TEXT NOT NULL DEFAULT 'CUI'
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sdc_timeline_design   ON sdc_compliance_timeline (design_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sdc_timeline_snapshot ON sdc_compliance_timeline (snapshot_label)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sdc_roi_design        ON sdc_roi_metrics (design_id)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 161 applied: sdc_compliance_timeline + sdc_roi_metrics created.")
