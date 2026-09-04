#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 020 — nc_topologies table for NDC network topology records.

tools/migration_intelligence/opportunity_scanner.py queries nc_topologies in
network_canvas.db (SELECT id, name FROM nc_topologies ORDER BY updated_at DESC).
This migration registers an equivalent CREATE TABLE in icdev.db so that:
  1. The gap detector's orphan_db_table rule can resolve the table definition.
  2. Deployments that consolidate network_canvas.db into icdev.db have the
     schema pre-applied.

Primary schema lives in tools/network/db/init_db.py (network_canvas.db).

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.

Reference: tools/migration_intelligence/opportunity_scanner.py:147
Gap task:   kanban/cdh-gap-02-d1
"""

MIGRATION_ID = "020"
MIGRATION_NAME = "nc_topologies_schema"
DESCRIPTION = "Register nc_topologies in icdev.db (gap fix — orphan_db_table rule)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_topologies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_nc_topologies_updated_at
    ON nc_topologies(updated_at DESC);
"""


def up(conn=None) -> None:
    """Apply migration: create nc_topologies table in icdev.db."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        _close = True
    else:
        _close = False

    conn.executescript(_SCHEMA)
    conn.commit()

    if _close:
        conn.close()

    print(f"[migration {MIGRATION_ID}] {DESCRIPTION} — applied")


if __name__ == "__main__":
    up()
