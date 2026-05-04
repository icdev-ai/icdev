#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 107 — sg_theaters: Combatant Command theater definitions.

Creates the sg_theaters table that seed_sg_theaters.py and eo_importer.py
reference via INSERT/SELECT. Columns match the seed script's write path:
  id, name, code, area_wkt, commander, status, priority, created_at, updated_at

sg_entities and sg_tracks carry a theater_id column that soft-references this
table (no FK enforced at DB level for cross-backend compat).
"""
from __future__ import annotations

from tools.db.storage import get_connection

_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS sg_theaters (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        code        TEXT NOT NULL UNIQUE,
        area_wkt    TEXT,
        commander   TEXT,
        status      TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'inactive', 'archived')),
        priority    INTEGER NOT NULL DEFAULT 99,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_sg_theaters_status ON sg_theaters(status)",
    "CREATE INDEX IF NOT EXISTS idx_sg_theaters_code   ON sg_theaters(code)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        print("[107_sg_theaters] up: sg_theaters table created")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
