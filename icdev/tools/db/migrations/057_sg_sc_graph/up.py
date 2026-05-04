#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 057 — Create sg_sc_nodes and sg_sc_edges tables.

Military supply chain graph for Strategos reverse cascade inference.
Nodes represent logistics entities (unit, depot, warehouse, raw_material,
factory, port).  Edges represent supply flows (source supplies target).

Reverse BFS from a unit node walks edges upstream to identify candidate
disruption sites.
"""
from tools.db.storage import get_connection

MIGRATION_ID = "057"
MIGRATION_NAME = "sg_sc_graph"
DESCRIPTION = "Create sg_sc_nodes and sg_sc_edges tables for supply chain graph"

_DDL_NODES = """
CREATE TABLE IF NOT EXISTS sg_sc_nodes (
    id            TEXT PRIMARY KEY,
    node_type     TEXT NOT NULL
                      CHECK(node_type IN ('unit','depot','warehouse',
                                          'raw_material','factory','port')),
    label         TEXT NOT NULL,
    criticality   TEXT NOT NULL DEFAULT 'medium'
                      CHECK(criticality IN ('critical','high','medium','low')),
    location      TEXT,
    metadata_json TEXT,
    created_at    TEXT NOT NULL
)
"""

_DDL_EDGES = """
CREATE TABLE IF NOT EXISTS sg_sc_edges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    edge_type     TEXT NOT NULL DEFAULT 'supplies',
    lag_days      INTEGER DEFAULT 0,
    metadata_json TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(source_id, target_id)
)
"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_sc_nodes_type ON sg_sc_nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sc_nodes_crit ON sg_sc_nodes(criticality)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sc_edges_src  ON sg_sc_edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sc_edges_tgt  ON sg_sc_edges(target_id)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(_DDL_NODES)
        conn.execute(_DDL_EDGES)
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("Migration 057 up: sg_sc_nodes and sg_sc_edges created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
