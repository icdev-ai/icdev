"""Migration 131 — create infra_canvas_designs table.

Canonical schema registration for the IDC infrastructure canvas designs table.
The table is queried by tools/idc/infra_scanner.py and tools/idc/hardening_checker.py;
this migration registers it so that gap detection (orphan_db_table rule) resolves.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS infra_canvas_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    design_json     TEXT NOT NULL DEFAULT '{}',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    status          TEXT NOT NULL DEFAULT 'draft',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_infra_canvas_designs_updated
    ON infra_canvas_designs (updated_at DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
