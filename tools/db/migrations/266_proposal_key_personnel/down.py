#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 266 rollback: drop proposal_key_personnel."""

MIGRATION_ID = "266"
MIGRATION_NAME = "proposal_key_personnel"


def down(conn) -> dict:
    conn.execute("DROP INDEX IF EXISTS idx_pkp_ref")
    conn.execute("DROP INDEX IF EXISTS idx_pkp_opp")
    conn.execute("DROP TABLE IF EXISTS proposal_key_personnel")
    conn.commit()
    return {"dropped": ["proposal_key_personnel"]}
