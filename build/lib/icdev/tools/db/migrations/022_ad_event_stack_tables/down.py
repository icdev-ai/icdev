#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 022 rollback — drops event_stack pillar tables."""

MIGRATION_ID = "022"
MIGRATION_NAME = "ad_event_stack_tables"

_TABLES = [
    "ad_earnings_history",
    "ad_analyst_actions",
    "ad_analyst_ratings",
    "ad_insider_transactions",
]

_INDEXES = [
    "idx_ad_earnings_history_ticker_date",
    "idx_ad_analyst_actions_ticker_date",
    "idx_ad_analyst_ratings_ticker_date",
    "idx_ad_insider_txn_ticker_date",
]


def down(conn) -> dict:
    actions = []

    for idx in _INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    actions.append("dropped_indexes")

    for table in _TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        actions.append(f"dropped_{table}")

    conn.commit()
    return {"status": "reverted", "actions": actions}
