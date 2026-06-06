#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 010 rollback: drop kanban_executions table.

Does not remove the executor_* columns from kanban_tasks (SQLite has no
DROP COLUMN). Use migration 012's rebuild path if column removal is needed.
"""

import sqlite3


def down(conn: sqlite3.Connection) -> dict:
    conn.execute("DROP TABLE IF EXISTS kanban_executions")
    conn.commit()
    return {"status": "rolled_back", "dropped": ["kanban_executions"]}
