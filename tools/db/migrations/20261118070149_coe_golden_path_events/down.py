# CUI // SP-CTI
"""Rollback 20261118070149 — drop the golden path ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS coe_golden_path_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS coe_golden_path_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
