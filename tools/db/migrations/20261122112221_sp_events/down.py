# CUI // SP-CTI
"""Rollback 20261122112221 — drop the store publisher ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS sp_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS sp_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
