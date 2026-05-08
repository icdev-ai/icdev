#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 113 rollback — drop kanban_task_comments (columns left in place)."""


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS kanban_task_comments")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    print(down())
