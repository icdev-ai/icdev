# CUI // SP-CTI
"""Rollback 20261119040000 — drop the objective-evidence ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS ec_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS ec_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
