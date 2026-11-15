# CUI // SP-CTI
"""Migration 20261114163435 rollback — drop the governance twin observations.

Dropping the table discards every recorded observation, and with it every
replay. It is a schema operation, not a routine one (NIST AU-9).
"""
from __future__ import annotations

_TAG = "[20261114163435_twin_governance_observations]"


def down(conn) -> dict:
    from tools.db.storage import is_pg
    from tools.twin_core.governance import TABLE

    conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    if is_pg(conn):
        conn.execute(f"DROP FUNCTION IF EXISTS {TABLE}_immutable()")
    conn.commit()
    print(f"{_TAG} {TABLE} dropped")
    return {"status": "reverted", "table": TABLE}
