#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 027: pipeline_snapshots — append-only DAG snapshot store.

Captures a versioned snapshot of a pipeline's node/edge topology per
snapshot_id. Used by the PDC Digital Twin layer for baseline + delta
comparison and pre-merge simulation.

Schema rationale:
  - nodes_json / edges_json stored separately (not combined in graph_json)
    so diff queries can target one dimension without deserialising the other.
  - meta_json holds auxiliary fields (configs, SLSA level, classification,
    antipattern scan results) without requiring schema migrations for each.
  - snapshot_type distinguishes baseline (frozen current state) from delta
    (proposed change under evaluation).

Append-only per NIST AU: snapshots are immutable once written; use a new
snapshot row rather than updating an existing one.

Idempotent — uses sqlite_master check before CREATE.
"""

import sqlite3

MIGRATION_ID = "027"
MIGRATION_NAME = "pipeline_snapshots"
DESCRIPTION = "Append-only DAG snapshot store for PDC Digital Twin (nodes_json + edges_json + meta)"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: create pipeline_snapshots (idempotent)."""
    actions = []
    cur = conn.cursor()

    if _table_exists(conn, "pipeline_snapshots"):
        actions.append("table_exists")
    else:
        cur.execute("""
            CREATE TABLE pipeline_snapshots (
                id             TEXT PRIMARY KEY,
                pipeline_id    TEXT NOT NULL,
                snapshot_type  TEXT NOT NULL DEFAULT 'baseline',
                label          TEXT,
                nodes_json     TEXT NOT NULL DEFAULT '[]',
                edges_json     TEXT NOT NULL DEFAULT '[]',
                meta_json      TEXT NOT NULL DEFAULT '{}',
                created_by     TEXT,
                created_at     TEXT NOT NULL
            )
        """)
        actions.append("created_table")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ps_pipeline "
        "ON pipeline_snapshots(pipeline_id, created_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ps_type "
        "ON pipeline_snapshots(snapshot_type, created_at DESC)"
    )
    actions.append("indexes_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
