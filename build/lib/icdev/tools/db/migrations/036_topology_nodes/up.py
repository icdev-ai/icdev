#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 036: Create topology_nodes table.

Resolves orphan_db_table gap for topology_nodes detected by the
Internal Awareness Engine (task-072a388a80).

tools/network/twin.py queries this table in take_snapshot():
  - take_snapshot(): counts devices per topology_id

No migration previously defined the schema, causing fresh deployments to
fail silently on the first snapshot call (device_count always returns 0
via the bare except guard).

The table is mutable design state (not append-only): nodes are
created/updated/deleted as the user edits the network topology graph.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS topology_nodes (
    id           TEXT        NOT NULL,
    topology_id  TEXT        NOT NULL,
    node_type    TEXT        NOT NULL DEFAULT 'device',
    label        TEXT        NOT NULL DEFAULT '',
    ip_address   TEXT        NOT NULL DEFAULT '',
    config_json  TEXT        NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS topology_nodes (
    id           TEXT    NOT NULL,
    topology_id  TEXT    NOT NULL,
    node_type    TEXT    NOT NULL DEFAULT 'device',
    label        TEXT    NOT NULL DEFAULT '',
    ip_address   TEXT    NOT NULL DEFAULT '',
    config_json  TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id)
)
"""

_IDX_TOPOLOGY = "CREATE INDEX IF NOT EXISTS idx_topology_nodes_topology_id ON topology_nodes(topology_id)"
_IDX_TYPE = "CREATE INDEX IF NOT EXISTS idx_topology_nodes_node_type ON topology_nodes(node_type)"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        ddl = _DDL if backend == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        conn.execute(_IDX_TOPOLOGY)
        conn.execute(_IDX_TYPE)
        conn.commit()
        print("[036_topology_nodes] up: topology_nodes created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
