# CUI // SP-CTI
"""Migration 20261114175314 rollback — remove the cortex_audit triggers.

Only the UPDATE/DELETE triggers go. The chain columns, their values and the
genesis row stay: they are audit evidence, and discarding them is not a
routine operation (NIST AU-9). With the triggers gone the chain still shows any
later change to a chained row.
"""
from __future__ import annotations

_TAG = "[20261114175314_cortex_audit_hash_chain]"


def down(conn) -> dict:
    from tools.cortex.db.audit_chain import GENESIS_TABLE, TABLE
    from tools.db.storage import is_pg

    for table in (TABLE, GENESIS_TABLE):
        for suffix in ("no_update", "no_delete"):
            if is_pg(conn):
                conn.execute(f"DROP TRIGGER IF EXISTS {table}_{suffix} ON {table}")
            else:
                conn.execute(f"DROP TRIGGER IF EXISTS {table}_{suffix}")
        if is_pg(conn):
            conn.execute(f"DROP FUNCTION IF EXISTS {table}_immutable()")
    conn.commit()
    print(f"{_TAG} cortex_audit triggers removed; chain columns and genesis kept")
    return {"status": "reverted"}
