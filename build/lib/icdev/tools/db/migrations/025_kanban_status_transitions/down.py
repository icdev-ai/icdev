#!/usr/bin/env python3
# CUI // SP-CTI
"""Revert migration 025: drop kanban_status_transitions."""

import sqlite3

MIGRATION_ID = "025"


def down(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS idx_kst_time")
    cur.execute("DROP INDEX IF EXISTS idx_kst_task")
    cur.execute("DROP TABLE IF EXISTS kanban_status_transitions")
    conn.commit()
    return {"status": "reverted", "actions": ["dropped_table_and_indexes"]}
