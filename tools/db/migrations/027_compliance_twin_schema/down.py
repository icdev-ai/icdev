#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 027 rollback — drop cATO Twin tables.

WARNING: Dropping compliance_twin_violations is irreversible if audit records
exist. Only apply in dev/test environments or when all data has been exported.
"""

import sqlite3

MIGRATION_ID = "027"


def down(conn: sqlite3.Connection) -> dict:
    actions = []
    for table in (
        "compliance_twin_violations",
        "compliance_twin_snapshots",
        "compliance_twin_runs",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        actions.append(f"dropped_{table}")
    conn.commit()
    return {"status": "rolled_back", "actions": actions}
