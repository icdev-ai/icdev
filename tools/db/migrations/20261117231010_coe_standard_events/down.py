# CUI // SP-CTI
"""Rollback 20261117231010 — drop the engineering standards lifecycle ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS coe_standard_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS coe_standard_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
