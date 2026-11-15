# CUI // SP-CTI
"""Rollback 20261115114944 — drop the auto-dispatch grants log.

The four ace_event_results columns are left in place: they default to '' and
nothing older reads them, and dropping a column loses the feed's history.
"""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS ace_dispatch_grants")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS ace_dispatch_grants_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
