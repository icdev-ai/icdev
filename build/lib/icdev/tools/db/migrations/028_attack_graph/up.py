#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 028: attack_graph_nodes + attack_graph_edges — SDC Digital Twin.

Formalizes the STRIDE/ATT&CK attack graph as a queryable, append-only
structure for the Security Design Canvas (SDC) Attack Path Twin.

Schema rationale:
  - attack_graph_nodes: assets participating in the attack graph.
    classification + value drive path-cost weighting (higher-value IL5
    assets raise the cost of paths that reach them).
  - attack_graph_edges: exploitable transitions between assets.
    ttp_id maps to a MITRE ATT&CK technique; cost is the attacker effort
    (lower = easier traversal); prereqs_json lists required antecedent
    conditions (e.g., ["cred_reuse", "no_mfa"]).
  - Both tables are append-only per NIST AU — attack simulation runs
    build a cumulative evidence trail. Supersede with a new record;
    never update or delete.

Idempotent — uses sqlite_master check before CREATE.
"""

import sqlite3

MIGRATION_ID = "028"
MIGRATION_NAME = "attack_graph"
DESCRIPTION = (
    "Append-only attack graph schema for SDC Digital Twin "
    "(attack_graph_nodes + attack_graph_edges)"
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: create attack_graph_nodes and attack_graph_edges (idempotent)."""
    actions = []
    cur = conn.cursor()

    if _table_exists(conn, "attack_graph_nodes"):
        actions.append("nodes_table_exists")
    else:
        cur.execute("""
            CREATE TABLE attack_graph_nodes (
                id              TEXT PRIMARY KEY,
                asset_id        TEXT NOT NULL,
                classification  TEXT NOT NULL DEFAULT 'CUI',
                value           REAL NOT NULL DEFAULT 1.0,
                label           TEXT,
                meta_json       TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )
        """)
        actions.append("created_nodes_table")

    if _table_exists(conn, "attack_graph_edges"):
        actions.append("edges_table_exists")
    else:
        cur.execute("""
            CREATE TABLE attack_graph_edges (
                id              TEXT PRIMARY KEY,
                src_node_id     TEXT NOT NULL,
                dst_node_id     TEXT NOT NULL,
                ttp_id          TEXT NOT NULL,
                cost            REAL NOT NULL DEFAULT 1.0,
                prereqs_json    TEXT NOT NULL DEFAULT '[]',
                meta_json       TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )
        """)
        actions.append("created_edges_table")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ag_nodes_asset "
        "ON attack_graph_nodes(asset_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ag_nodes_classification "
        "ON attack_graph_nodes(classification)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ag_edges_src "
        "ON attack_graph_edges(src_node_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ag_edges_dst "
        "ON attack_graph_edges(dst_node_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ag_edges_ttp "
        "ON attack_graph_edges(ttp_id)"
    )
    actions.append("indexes_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
