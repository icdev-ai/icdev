# CUI // SP-CTI
"""Rollback 20261118113000 — drop the data discovery export ledger."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS data_discovery_export_events")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS data_discovery_export_events_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
