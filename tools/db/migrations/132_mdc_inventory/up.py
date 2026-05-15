"""Migration 132 — create mdc_inventory table.

Canonical schema registration for the Migration Design Canvas inventory table.
Queried by tools/mdc/inventory_scanner.py to load per-project asset counts
and 7Rs distribution. Resolves the orphan_db_table gap detected by
tools/awareness/gap_detector.py.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS mdc_inventory (
    id           SERIAL PRIMARY KEY,
    project_id   TEXT NOT NULL,
    metric_key   TEXT NOT NULL,
    metric_value TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
);

CREATE INDEX IF NOT EXISTS idx_mdc_inventory_project
    ON mdc_inventory (project_id, created_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
