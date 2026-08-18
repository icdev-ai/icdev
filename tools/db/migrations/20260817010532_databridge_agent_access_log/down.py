#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback 20260817010532: drop ``databridge_agent_access_log``.

Dropping the table destroys the access decisions it holds, and there is no
non-destructive rollback of a CREATE TABLE. This is for un-applying the
migration on a database that never carried real decisions — a fresh worktree, an
ephemeral CI database, a failed first apply.

The DDL is NOT imported from ``up.py``: a migration ``down`` module cannot
``from .up import ...`` (the migration directory is loaded by path, not as a
package), so the table name is repeated here deliberately.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_STATEMENTS = [
    "DROP INDEX IF EXISTS idx_db_agent_access_tenant",
    "DROP INDEX IF EXISTS idx_db_agent_access_decision",
    "DROP INDEX IF EXISTS idx_db_agent_access_agent",
    "DROP TABLE IF EXISTS databridge_agent_access_log",
]


def down(conn=None) -> None:
    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()
    try:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
        if _own_conn:
            conn.commit()
        print(
            "[20260817010532_databridge_agent_access_log] down: "
            "databridge_agent_access_log dropped"
        )
    finally:
        if _own_conn:
            conn.close()


if __name__ == "__main__":
    down()
