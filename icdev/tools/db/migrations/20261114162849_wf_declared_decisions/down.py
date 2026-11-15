# CUI // SP-CTI
"""Rollback 20261114162849 — drop the declared-decision ledger and adjudications."""
from __future__ import annotations


def down(conn) -> dict:
    for table in ("cortex_finding_adjudications", "wf_decision_steps"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    from tools.db.storage import is_pg

    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS wf_declared_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
