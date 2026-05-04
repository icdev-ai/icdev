#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 022: Create orphan ad_* tables for the event_stack confluence pillar.

Resolves gap op-gap-b36d2dd80b7245b1 detected by the Internal Awareness Engine.

Tables created:
  - ad_earnings_history       — per-ticker EPS/revenue beat data (surprise_pct drives the
                                 earnings_beat vote in event_stack.py)
  - ad_analyst_actions        — analyst upgrades/downgrades/price-target changes
  - ad_analyst_ratings        — analyst rating changes (change column = upgrade/downgrade)
  - ad_insider_transactions   — officer/director open-market buys and sells

All statements are idempotent (CREATE TABLE IF NOT EXISTS).
"""

MIGRATION_ID = "022"
MIGRATION_NAME = "ad_event_stack_tables"
DESCRIPTION = (
    "Create 4 orphan ad_* tables required by the event_stack confluence pillar. "
    "Resolves schema drift — tables queried in event_stack.py but absent from any "
    "migration, causing fresh deployments to 500 on first use."
)

_CREATE_STMTS = [
    # ad_earnings_history — first referenced in event_stack.py:_last_earnings_beat()
    """CREATE TABLE IF NOT EXISTS ad_earnings_history (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        report_date TEXT NOT NULL,
        surprise_pct REAL,
        eps_actual REAL,
        eps_estimate REAL,
        revenue_actual REAL,
        revenue_estimate REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # ad_analyst_actions — first referenced in event_stack.py:_recent_analyst_upgrade()
    """CREATE TABLE IF NOT EXISTS ad_analyst_actions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        action TEXT DEFAULT '',
        analyst TEXT DEFAULT '',
        firm TEXT DEFAULT '',
        price_target REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # ad_analyst_ratings — first referenced in event_stack.py:_recent_analyst_upgrade()
    """CREATE TABLE IF NOT EXISTS ad_analyst_ratings (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        change TEXT DEFAULT '',
        rating TEXT DEFAULT '',
        analyst TEXT DEFAULT '',
        firm TEXT DEFAULT '',
        price_target REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # ad_insider_transactions — first referenced in event_stack.py:_recent_insider_buy()
    """CREATE TABLE IF NOT EXISTS ad_insider_transactions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        transaction_type TEXT DEFAULT '',
        insider_name TEXT DEFAULT '',
        insider_title TEXT DEFAULT '',
        shares REAL,
        price REAL,
        value REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ad_earnings_history_ticker_date ON ad_earnings_history (ticker, report_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ad_analyst_actions_ticker_date ON ad_analyst_actions (ticker, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ad_analyst_ratings_ticker_date ON ad_analyst_ratings (ticker, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ad_insider_txn_ticker_date ON ad_insider_transactions (ticker, created_at DESC)",
]


def up(conn) -> dict:
    """Apply migration: create event_stack pillar tables (idempotent)."""
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
