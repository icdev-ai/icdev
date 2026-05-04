#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 064 — hitl_items (HITL action tracking)."""

from tools.db.storage import get_connection

_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS hitl_items (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        wri_id     INTEGER,
        action_text TEXT,
        status     VARCHAR(64) NOT NULL DEFAULT 'PENDING',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
]


def up(conn=None):
    conn = get_connection()
    try:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        print("[064_sg_hitl_items_type] migration up complete")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
