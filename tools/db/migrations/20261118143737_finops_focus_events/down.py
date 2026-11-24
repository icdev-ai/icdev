# CUI // SP-CTI
"""Rollback 20261118143737 — drop the FOCUS cost bundle ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS finops_focus_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS finops_focus_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
