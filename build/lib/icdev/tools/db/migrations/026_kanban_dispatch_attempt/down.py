#!/usr/bin/env python3
# CUI // SP-CTI
"""Revert migration 026: drop dispatch_attempt_id column + index."""

import sqlite3

MIGRATION_ID = "026"


def down(conn: sqlite3.Connection) -> dict:
    # SQLite < 3.35 can't DROP COLUMN. We drop the index and leave the
    # column; it will be silently ignored by later code (nullable TEXT).
    cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS idx_kanban_dispatch_attempt")
    conn.commit()
    return {"status": "reverted", "actions": ["dropped_index_only"]}
