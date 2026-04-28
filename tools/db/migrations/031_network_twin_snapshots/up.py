#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 031: Create network_twin_snapshots table.

Resolves orphan_db_table gap for network_twin_snapshots detected by the
Internal Awareness Engine (task-838fffd611).

tools/network/twin.py:take_snapshot() inserts into this table and
tools/network/blueprint.py reads from it, but no migration previously
defined the schema — causing fresh deployments to silently drop snapshots.

Table is append-only (NIST AU): topology state snapshots are immutable
history records used for delta simulation and blast-radius analysis.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS network_twin_snapshots (
    id            TEXT        NOT NULL,
    project_id    TEXT        NOT NULL,
    label         TEXT,
    device_count  INTEGER     NOT NULL DEFAULT 0,
    link_count    INTEGER     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS network_twin_snapshots (
    id            TEXT    NOT NULL,
    project_id    TEXT    NOT NULL,
    label         TEXT,
    device_count  INTEGER NOT NULL DEFAULT 0,
    link_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id)
)
"""

_IDX = "CREATE INDEX IF NOT EXISTS idx_nts_project_id ON network_twin_snapshots(project_id)"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        ddl = _DDL if backend == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        conn.execute(_IDX)
        conn.commit()
        print("[031_network_twin_snapshots] up: network_twin_snapshots created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
