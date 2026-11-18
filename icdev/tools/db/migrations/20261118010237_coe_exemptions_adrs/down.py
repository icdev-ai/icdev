# CUI // SP-CTI
"""Rollback 20261118010237 — drop the ADR ledger and the exemption log's triggers.

The columns added to ``idp_rule_exemptions`` are left in place: dropping a
column would erase the scope, digest and step of rows already written, and the
log is a record. The append-only triggers are dropped so the table returns to
its pre-migration state.
"""
from __future__ import annotations


def down(conn) -> dict:
    from tools.db.storage import is_pg

    conn.execute("DROP TABLE IF EXISTS coe_adr_events")
    for op in ("update", "delete"):
        conn.execute(f"DROP TRIGGER IF EXISTS idp_rule_exemptions_no_{op}"
                     + (" ON idp_rule_exemptions" if is_pg(conn) else ""))
    if is_pg(conn):
        conn.execute("DROP FUNCTION IF EXISTS coe_adr_events_immutable()")
        conn.execute("DROP FUNCTION IF EXISTS idp_rule_exemptions_immutable()")
    conn.commit()
    return {"status": "rolled_back"}
