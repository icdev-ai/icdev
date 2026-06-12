"""Migration 138 — create ohc_runbooks table.

Canonical schema registration for the OHC runbook checker table.
Queried by tools/ohc/runbook_checker.py to load per-project runbook
completeness and automation readiness checks.
Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS ohc_runbooks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    check_key   TEXT NOT NULL,
    check_value TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
);

CREATE INDEX IF NOT EXISTS idx_ohc_runbooks_project
    ON ohc_runbooks (project_id, created_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
