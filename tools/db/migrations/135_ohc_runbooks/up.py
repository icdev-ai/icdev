#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 135 — create ohc_runbooks and ohc_ops_metrics tables.

Resolves orphan_db_table gap for tables referenced by:
  tools/ohc/runbook_checker.py  (ohc_runbooks)
  tools/ohc/ops_scanner.py      (ohc_ops_metrics)
"""

SQL = """
CREATE TABLE IF NOT EXISTS ohc_runbooks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT 'default',
    check_key   TEXT NOT NULL DEFAULT '',
    check_value TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ohc_runbooks_project
    ON ohc_runbooks (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ohc_ops_metrics (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL DEFAULT 'default',
    metric_key   TEXT NOT NULL DEFAULT '',
    metric_value TEXT DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ohc_ops_metrics_project
    ON ohc_ops_metrics (project_id, created_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
