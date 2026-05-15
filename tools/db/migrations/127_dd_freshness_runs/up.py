"""Migration 127 — create dd_freshness_runs table.

Tracks Data Canvas freshness check runs (tools/data_canvas/freshness_guardian.py).
Append-only audit trail per NIST AU requirements.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS dd_freshness_runs (
    run_id         TEXT PRIMARY KEY,
    design_id      TEXT,
    overall_status TEXT NOT NULL DEFAULT 'unknown',
    result_json    TEXT NOT NULL DEFAULT '{}',
    run_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dd_freshness_design
    ON dd_freshness_runs (design_id);

CREATE INDEX IF NOT EXISTS idx_dd_freshness_run_at
    ON dd_freshness_runs (run_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
