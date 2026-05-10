#!/usr/bin/env python3
"""FathomDesk Database Initialization — creates 14 ad_* tables in the main ICDEV database."""

import argparse
import json
import sys
from pathlib import Path

# Ensure ICDEV root is on the path (apps/fathomdesk/tools/trading/ → 4 parents = ICDev/)
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.db.storage import get_connection  # noqa: E402

TABLES = [
    """CREATE TABLE IF NOT EXISTS ad_portfolios (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT 'Default',
        cash_balance REAL NOT NULL DEFAULT 100000.0,
        strategy TEXT DEFAULT 'balanced',
        risk_profile TEXT DEFAULT 'moderate',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_watchlists (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        asset_class TEXT NOT NULL DEFAULT 'equity',
        target_price REAL,
        alert_enabled INTEGER DEFAULT 0,
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_positions (
        id TEXT PRIMARY KEY,
        portfolio_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        qty REAL NOT NULL,
        avg_cost REAL NOT NULL,
        market_value REAL DEFAULT 0.0,
        unrealized_pnl REAL DEFAULT 0.0,
        asset_class TEXT DEFAULT 'equity',
        alpaca_position_id TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_orders (
        id TEXT PRIMARY KEY,
        portfolio_id TEXT,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL CHECK(side IN ('buy','sell')),
        qty REAL NOT NULL,
        order_type TEXT NOT NULL DEFAULT 'market',
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','submitted','filled','cancelled','rejected')),
        fill_price REAL,
        alpaca_order_id TEXT,
        signal_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        filled_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_signals (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('BUY','SELL','HOLD')),
        composite_score REAL NOT NULL,
        confidence REAL NOT NULL,
        component_scores TEXT,
        run_id TEXT,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','executed','expired')),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        actioned_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_analysis_runs (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        workflow_name TEXT DEFAULT 'analysis',
        status TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed')),
        duration_ms INTEGER,
        result_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_analyst_reports (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        analyst_type TEXT NOT NULL CHECK(analyst_type IN ('fundamental','sentiment','news','technical')),
        findings_json TEXT NOT NULL,
        score REAL,
        confidence REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_debate_records (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        bull_thesis TEXT,
        bear_thesis TEXT,
        bull_conviction REAL,
        bear_conviction REAL,
        net_score REAL,
        consensus INTEGER DEFAULT 0,
        synthesis TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_risk_assessments (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        passed INTEGER NOT NULL DEFAULT 1,
        checks_json TEXT,
        warnings TEXT,
        var_1d REAL,
        max_drawdown REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_trade_decisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('BUY','SELL','HOLD')),
        direction TEXT,
        suggested_qty REAL,
        entry_price REAL,
        stop_loss REAL,
        take_profit REAL,
        approved INTEGER DEFAULT 0,
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_market_data_cache (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT '1Day',
        asset_class TEXT DEFAULT 'equity',
        ohlcv_json TEXT NOT NULL,
        bar_count INTEGER,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_strategy_configs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        params_json TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_performance_metrics (
        id TEXT PRIMARY KEY,
        portfolio_id TEXT NOT NULL,
        date TEXT NOT NULL,
        daily_pnl REAL DEFAULT 0.0,
        cumulative_pnl REAL DEFAULT 0.0,
        sharpe_ratio REAL,
        win_rate REAL,
        max_drawdown REAL,
        total_trades INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_pulse_articles (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        pulse_post_id TEXT,
        article_topic TEXT,
        status TEXT DEFAULT 'draft' CHECK(status IN ('draft','published','failed')),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]


def init_db(db_path=None):  # db_path kept for backward compat but ignored
    """Initialize FathomDesk tables in the main ICDEV database."""
    conn = get_connection()
    created = []
    for sql in TABLES:
        conn.execute(sql)
        name = sql.split("IF NOT EXISTS ")[1].split(" (")[0].strip()
        created.append(name)
    conn.commit()
    conn.close()
    return {"status": "ok", "tables_created": len(created), "tables": created, "backend": "icdev_main"}


def main():
    parser = argparse.ArgumentParser(description="FathomDesk DB Init")
    parser.add_argument("--db-path", help="Deprecated — ignored; tables go into main ICDEV database")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = init_db()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Created {result['tables_created']} tables in main ICDEV database")
        for t in result['tables']:
            print(f"  - {t}")


if __name__ == "__main__":
    main()
