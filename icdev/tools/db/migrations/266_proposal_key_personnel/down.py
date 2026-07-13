#!/usr/bin/env python3
# CUI // SP-CTI
"""Down-migration for 266: drop proposal_key_personnel."""

MIGRATION_ID = "266"
MIGRATION_NAME = "proposal_key_personnel"


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS proposal_key_personnel")
    conn.commit()
    return {"status": "reverted", "actions": ["dropped proposal_key_personnel"]}
