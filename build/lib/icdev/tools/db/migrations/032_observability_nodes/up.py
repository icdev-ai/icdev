#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 032: Create observability_nodes table.

Resolves orphan_db_table gap for observability_nodes detected by the
Internal Awareness Engine.

tools/observability_canvas/twin.py:take_snapshot() queries this table to
count service-type nodes per design, but no migration previously defined
the schema — causing fresh deployments to fail with OperationalError on
the first snapshot attempt.

Columns mirror the node structure used by the Observability Design Canvas
(design_id + node_type + spatial/label metadata). The table is mutable
design state (not append-only): nodes are created/updated/deleted as the
user edits the observability design graph.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS observability_nodes (
    id           TEXT        NOT NULL,
    design_id    TEXT        NOT NULL,
    node_type    TEXT        NOT NULL,
    label        TEXT        NOT NULL DEFAULT '',
    x            REAL        NOT NULL DEFAULT 0,
    y            REAL        NOT NULL DEFAULT 0,
    config_json  TEXT        NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS observability_nodes (
    id           TEXT    NOT NULL,
    design_id    TEXT    NOT NULL,
    node_type    TEXT    NOT NULL,
    label        TEXT    NOT NULL DEFAULT '',
    x            REAL    NOT NULL DEFAULT 0,
    y            REAL    NOT NULL DEFAULT 0,
    config_json  TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id)
)
"""

_IDX_DESIGN = "CREATE INDEX IF NOT EXISTS idx_obs_nodes_design_id ON observability_nodes(design_id)"
_IDX_TYPE = "CREATE INDEX IF NOT EXISTS idx_obs_nodes_node_type ON observability_nodes(node_type)"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        ddl = _DDL if backend == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        conn.execute(_IDX_DESIGN)
        conn.execute(_IDX_TYPE)
        conn.commit()
        print("[032_observability_nodes] up: observability_nodes created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
