# CUI // SP-CTI
"""Rollback 20261118140000 — drop the AI-BOM export ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS aibom_export_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS aibom_export_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
