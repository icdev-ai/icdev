-- Migration 057: ad_backtest_runs — FathomDesk backtest result store (NIST AU — append-only)
-- Stores one row per completed backtest run; never updated or deleted.

CREATE TABLE IF NOT EXISTS ad_backtest_runs (
    id                TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    strategy_id       TEXT NOT NULL,
    backtest_start    TEXT NOT NULL,
    backtest_end      TEXT NOT NULL,
    sharpe_ratio      REAL,
    calmar_ratio      REAL,
    max_drawdown_pct  REAL,
    win_rate          REAL,
    trade_count       INT,
    triggered_by      TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_backtest_runs_ticker      ON ad_backtest_runs(ticker);
CREATE INDEX IF NOT EXISTS idx_ad_backtest_runs_strategy    ON ad_backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_ad_backtest_runs_created_at  ON ad_backtest_runs(created_at);
