#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 024 rollback — drops ad_news_catalysts table."""

MIGRATION_ID = "024"
MIGRATION_NAME = "ad_news_catalysts"

_TABLES = [
    "ad_news_catalysts",
]

_INDEXES = [
    "idx_ad_news_catalysts_ticker_date",
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
