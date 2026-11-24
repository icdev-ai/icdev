# CUI // SP-CTI
"""Rollback 20261118104743 — drop the FORGE Academy CoP ledger and the learners' pseudonyms."""
from __future__ import annotations


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS cop_academy_events")
    conn.execute("DROP TABLE IF EXISTS cop_academy_learners")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS cop_academy_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
