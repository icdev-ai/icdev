#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 033: Create odc_twin_snapshots table.

Resolves orphan_db_table gap for odc_twin_snapshots detected by the
Internal Awareness Engine (task-7e3b99ff42).

tools/observability_canvas/twin.py:take_snapshot() inserts into this table and
tools/observability_canvas/blueprint.py reads from it, but no migration previously
defined the schema — causing fresh deployments to silently drop snapshots.

Table is append-only (NIST AU): OTel collector topology and coverage snapshots
are immutable history records used for delta simulation and SLO projection.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS odc_twin_snapshots (
    id              TEXT        NOT NULL,
    design_id       TEXT        NOT NULL,
    label           TEXT,
    service_count   INTEGER     NOT NULL DEFAULT 0,
    coverage_score  REAL        NOT NULL DEFAULT 0.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS odc_twin_snapshots (
    id              TEXT    NOT NULL,
    design_id       TEXT    NOT NULL,
    label           TEXT,
    service_count   INTEGER NOT NULL DEFAULT 0,
    coverage_score  REAL    NOT NULL DEFAULT 0.0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id)
)
"""

_IDX = "CREATE INDEX IF NOT EXISTS idx_ots_design_id ON odc_twin_snapshots(design_id)"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        ddl = _DDL if backend == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        conn.execute(_IDX)
        conn.commit()
        print("[033_odc_twin_snapshots] up: odc_twin_snapshots created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
