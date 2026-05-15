"""Migration 134 — create odc_designs table.

Canonical schema registration for the ODC (Observability Design Canvas) designs
table. The table is queried by tools/odc/coverage_scanner.py and
tools/odc/gap_checker.py; this migration registers it so that gap detection
(orphan_db_table rule) resolves.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS odc_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    status          TEXT NOT NULL DEFAULT 'draft',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_odc_designs_updated
    ON odc_designs (updated_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
