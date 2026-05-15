"""Migration 135 — create sdc_designs table.

Canonical schema registration for the SDC (Security Design Canvas) designs
table. The table is queried by tools/sdc/threat_scanner.py and
tools/sdc/stig_checker.py; this migration registers it so that gap detection
(orphan_db_table rule) resolves.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS sdc_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    status          TEXT NOT NULL DEFAULT 'draft',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sdc_designs_updated
    ON sdc_designs (updated_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
