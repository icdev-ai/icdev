#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 024: Create ad_news_catalysts table for the event_stack confluence pillar.

Resolves orphan_db_table gap op-gap-21e3fd3bb4e15147 detected by the Internal
Awareness Engine. Companion to migration 022 (ad_event_stack_tables) which created
the other four event_stack tables but missed ad_news_catalysts.

Table created:
  - ad_news_catalysts — per-ticker news headlines with sentiment labels; drives
                        the news_cluster_bull vote in event_stack.py:_news_cluster()

All statements are idempotent (CREATE TABLE IF NOT EXISTS).
"""

MIGRATION_ID = "024"
MIGRATION_NAME = "ad_news_catalysts"
DESCRIPTION = (
    "Create ad_news_catalysts table referenced by event_stack.py:_news_cluster() "
    "but absent from any migration, causing fresh deployments to 500 on first use. "
    "Companion to migration 022."
)

_CREATE_STMTS = [
    # ad_news_catalysts — referenced in event_stack.py:_news_cluster()
    """CREATE TABLE IF NOT EXISTS ad_news_catalysts (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        headline TEXT DEFAULT '',
        source TEXT DEFAULT '',
        sentiment TEXT DEFAULT '',
        url TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ad_news_catalysts_ticker_date ON ad_news_catalysts (ticker, created_at DESC)",
]


def up(conn) -> dict:
    """Apply migration: create ad_news_catalysts table (idempotent)."""
    actions = []
    errors = []

    for stmt in _CREATE_STMTS:
        table = stmt.strip().split("EXISTS")[1].split("(")[0].strip()
        try:
            conn.execute(stmt)
            actions.append(f"created_or_verified_{table}")
        except Exception as exc:
            errors.append(f"{table}: {exc}")

    for idx_stmt in _INDEXES:
        try:
            conn.execute(idx_stmt)
        except Exception as exc:
            errors.append(f"index_skipped: {exc}")

    conn.commit()
    return {
        "status": "applied" if not errors else "partial",
        "actions": actions,
        "errors": errors,
    }
