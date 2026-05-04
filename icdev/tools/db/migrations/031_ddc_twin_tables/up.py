#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 031: create DDC digital twin tables.

Resolves orphan_db_table gap for data_nodes, data_edges, and
data_twin_snapshots — tables referenced by tools/data_canvas/twin.py
but missing from any migration, causing fresh deployments to fail
on first snapshot call.

All CREATE TABLE statements are idempotent (IF NOT EXISTS).
"""

import sqlite3

MIGRATION_ID = "031"
MIGRATION_NAME = "ddc_twin_tables"
DESCRIPTION = (
    "Create data_nodes, data_edges, and data_twin_snapshots tables "
    "referenced by tools/data_canvas/twin.py. Resolves orphan_db_table "
    "gap flagged by the Internal Awareness Engine gap detector."
)

_TABLES = ["data_nodes", "data_edges", "data_twin_snapshots"]

_CREATE_STMTS = [
    # data_nodes — referenced in twin.py: SELECT COUNT(*) FROM data_nodes WHERE design_id=? AND node_type='table'
    """CREATE TABLE IF NOT EXISTS data_nodes (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    node_type   TEXT DEFAULT '',
    label       TEXT DEFAULT '',
    metadata    TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at  TEXT
)""",
    # data_edges — referenced in twin.py: SELECT COUNT(*) FROM data_edges WHERE design_id=?
    """CREATE TABLE IF NOT EXISTS data_edges (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    edge_type   TEXT DEFAULT '',
    label       TEXT DEFAULT '',
    metadata    TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at  TEXT
)""",
    # data_twin_snapshots — referenced in twin.py: INSERT INTO data_twin_snapshots (id, design_id, label, table_count, edge_count, classification, created_at)
    """CREATE TABLE IF NOT EXISTS data_twin_snapshots (
    id           TEXT PRIMARY KEY,
    design_id    TEXT NOT NULL,
    label        TEXT DEFAULT '',
    table_count  INTEGER DEFAULT 0,
    edge_count   INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at   TEXT
)""",
]

_INDEX_STMTS = [
    "CREATE INDEX IF NOT EXISTS idx_data_nodes_design ON data_nodes(design_id)",
    "CREATE INDEX IF NOT EXISTS idx_data_nodes_type ON data_nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_data_edges_design ON data_edges(design_id)",
    "CREATE INDEX IF NOT EXISTS idx_data_twin_snapshots_design ON data_twin_snapshots(design_id)",
]


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: create DDC twin tables (idempotent)."""
    created = 0
    errors = []

    for table, stmt in zip(_TABLES, _CREATE_STMTS):
        try:
            conn.execute(stmt)
            created += 1
        except sqlite3.OperationalError as exc:
            errors.append(f"{table}: {exc}")

    for stmt in _INDEX_STMTS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    return {
        "status": "applied" if not errors else "partial",
        "created_or_verified": created,
        "errors": errors,
        "total": len(_TABLES),
    }
