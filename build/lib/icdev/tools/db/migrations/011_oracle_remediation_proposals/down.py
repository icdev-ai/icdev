#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 011 rollback: drop oracle/finding tables.

WARNING: oracle_predictions, oracle_remediation_proposals,
oracle_convergence_events, and finding_approvals are append-only audit
tables. Rolling back this migration destroys audit history. Only use in
recovery scenarios.
"""

import sqlite3


def down(conn: sqlite3.Connection) -> dict:
    tables = [
        "oracle_convergence_events",
        "oracle_remediation_proposals",
        "oracle_predictions",
        "finding_approvals",
    ]
    for t in tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    return {"status": "rolled_back", "dropped": tables}
