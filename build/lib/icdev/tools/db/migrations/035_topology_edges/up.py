#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 035: Create topology_edges table.

Resolves orphan_db_table gap for topology_edges detected by the
Internal Awareness Engine (task-74e2abec00).

tools/network/twin.py queries this table in two places:
  - take_snapshot(): counts links per topology_id
  - blast_radius(): selects edges by source/target for impact analysis

No migration previously defined the schema, causing fresh deployments to
fail silently on the first snapshot or blast-radius call.

The table is mutable design state (not append-only): edges are
created/updated/deleted as the user edits the network topology graph.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS topology_edges (
    id           TEXT        NOT NULL,
    topology_id  TEXT        NOT NULL,
    source       TEXT        NOT NULL,
    target       TEXT        NOT NULL,
    label        TEXT        NOT NULL DEFAULT '',
    protocol     TEXT        NOT NULL DEFAULT '',
    bandwidth    TEXT        NOT NULL DEFAULT '',
    config_json  TEXT        NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS topology_edges (
    id           TEXT    NOT NULL,
    topology_id  TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    target       TEXT    NOT NULL,
    label        TEXT    NOT NULL DEFAULT '',
    protocol     TEXT    NOT NULL DEFAULT '',
    bandwidth    TEXT    NOT NULL DEFAULT '',
    config_json  TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id)
)
"""

_IDX_TOPOLOGY = "CREATE INDEX IF NOT EXISTS idx_topology_edges_topology_id ON topology_edges(topology_id)"
_IDX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_topology_edges_source ON topology_edges(source)"
_IDX_TARGET = "CREATE INDEX IF NOT EXISTS idx_topology_edges_target ON topology_edges(target)"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        ddl = _DDL if backend == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        conn.execute(_IDX_TOPOLOGY)
        conn.execute(_IDX_SOURCE)
        conn.execute(_IDX_TARGET)
        conn.commit()
        print("[035_topology_edges] up: topology_edges created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
