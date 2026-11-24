# CUI // SP-CTI
"""Rollback 20261118231119 — drop the vulnerability discovery ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS vr_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS vr_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
