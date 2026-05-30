"""Migration 133 — create mdc_readiness table.

Canonical schema registration for the Migration Design Canvas readiness table.
Queried by tools/mdc/readiness_checker.py to load per-project readiness checks
(landing zone, DMS, compliance gate). Resolves the orphan_db_table gap detected
by tools/awareness/gap_detector.py.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS mdc_readiness (
    id           SERIAL PRIMARY KEY,
    project_id   TEXT NOT NULL,
    check_key    TEXT NOT NULL,
    check_value  TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
);

CREATE INDEX IF NOT EXISTS idx_mdc_readiness_project
    ON mdc_readiness (project_id, created_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
