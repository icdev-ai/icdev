#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 010: Network Intelligence schema registration.

tools/network/network_intelligence.py writes to the 'topologies' table (and
related NI tables) in network_canvas.db.  The primary schema is defined in
tools/network/db/init_db.py and is applied to network_canvas.db by calling
tools.network.db.init_db.init_db().

This migration registers the same CREATE TABLE statements in icdev.db so that:
  1. The gap detector's orphan_db_table rule can resolve the table definition.
  2. Deployments that consolidate network_canvas.db into icdev.db have the
     schema pre-applied.

Safe to re-run: all statements use CREATE TABLE IF NOT EXISTS.

Reference: tools/network/db/init_db.py (authoritative schema)
Gap task:   kanban/task-e73a49df72
"""

MIGRATION_ID = "010"
MIGRATION_NAME = "network_intelligence_schema"
DESCRIPTION = (
    "Register topologies, ni_devices, ni_analyses, ni_state_snapshots "
    "in icdev.db (gap fix — orphan_db_table rule)"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topologies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    tags            TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ni_devices (
    id                      TEXT PRIMARY KEY,
    topology_id             TEXT REFERENCES topologies(id),
    node_id                 TEXT NOT NULL,
    label                   TEXT,
    device_type             TEXT,
    vendor                  TEXT,
    model                   TEXT,
    firmware_version        TEXT,
    eol_date                TEXT,
    eos_date                TEXT,
    purchase_date           TEXT,
    purchase_cost           REAL DEFAULT 0,
    replacement_cost        REAL DEFAULT 0,
    criticality             TEXT DEFAULT 'YELLOW',
    site                    TEXT,
    rack                    TEXT,
    maintenance_contract    TEXT,
    contract_expiry         TEXT,
    notes                   TEXT,
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ni_analyses (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id),
    analysis_type   TEXT NOT NULL,
    query_text      TEXT,
    input_json      TEXT DEFAULT '{}',
    result_json     TEXT DEFAULT '{}',
    result_summary  TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ni_state_snapshots (
    id                  TEXT PRIMARY KEY,
    topology_id         TEXT REFERENCES topologies(id),
    snapshot_type       TEXT DEFAULT 'manual',
    graph_json          TEXT,
    device_count        INTEGER DEFAULT 0,
    link_count          INTEGER DEFAULT 0,
    change_description  TEXT,
    created_by          TEXT,
    version             INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""


def up(conn) -> None:
    """Apply migration: create network intelligence tables in icdev.db."""
    conn.executescript(_SCHEMA)
    conn.commit()
    print(f"[migration {MIGRATION_ID}] {DESCRIPTION} — applied")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parents[4]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from tools.db.storage import get_connection

    _conn = get_connection()
    up(_conn)
    _conn.close()
