#!/usr/bin/env python3
"""Tests for SignalForge Backtester."""
# CUI // SP-CTI

import numpy as np
import pandas as pd
import pytest

from tools.trading.backtester import (
    simulate_trades,
    compute_metrics,
    monte_carlo,
    overfitting_score,
)


@pytest.fixture
def backtest_df():
    """DataFrame with OHLCV for backtesting."""
    np.random.seed(42)
    n = 200
    base = 5200.0
    close = base + np.cumsum(np.random.normal(0, 2, n))
    return pd.DataFrame({
        "close": close,
        "high": close + np.abs(np.random.normal(0, 3, n)),
        "low": close - np.abs(np.random.normal(0, 3, n)),
        "open": close + np.random.normal(0, 1, n),
        "volume": np.random.randint(100, 5000, n),
    })


@pytest.fixture
def sample_trades():
    """Pre-built list of trade results."""
    return [
        {"pnl_dollars": 500, "bars_held": 5, "exit_reason": "take_profit", "direction": "LONG"},
        {"pnl_dollars": -250, "bars_held": 3, "exit_reason": "stop_loss", "direction": "LONG"},
        {"pnl_dollars": 300, "bars_held": 8, "exit_reason": "take_profit", "direction": "SHORT"},
        {"pnl_dollars": -200, "bars_held": 4, "exit_reason": "stop_loss", "direction": "SHORT"},
        {"pnl_dollars": 400, "bars_held": 6, "exit_reason": "take_profit", "direction": "LONG"},
    ]


class TestSimulateTrades:
    """Test trade simulation from predictions."""

    def test_flat_predictions_no_trades(self, backtest_df, sample_config):
        """All FLAT predictions should produce no trades."""
        preds = np.ones(len(backtest_df), dtype=int)  # 1 = FLAT
        trades = simulate_trades(preds, backtest_df, sample_config)
        assert len(trades) == 0

    def test_long_predictions_produce_trades(self, backtest_df, sample_config):
        """LONG predictions should produce trades."""
        preds = np.full(len(backtest_df), 2, dtype=int)  # 2 = LONG
        trades = simulate_trades(preds, backtest_df, sample_config)
        assert len(trades) > 0
        assert all(t["direction"] == "LONG" for t in trades)

    def test_short_predictions_produce_trades(self, backtest_df, sample_config):
        """SHORT predictions should produce trades."""
        preds = np.zeros(len(backtest_df), dtype=int)  # 0 = SHORT
        trades = simulate_trades(preds, backtest_df, sample_config)
        assert len(trades) > 0
        assert all(t["direction"] == "SHORT" for t in trades)

    def test_trade_has_required_fields(self, backtest_df, sample_config):
        preds = np.full(len(backtest_df), 2, dtype=int)
        trades = simulate_trades(preds, backtest_df, sample_config)
        assert len(trades) > 0
        trade = trades[0]
        assert "bar_index" in trade
        assert "direction" in trade
        assert "entry" in trade
        assert "exit" in trade
        assert "pnl_points" in trade
        assert "pnl_dollars" in trade
        assert "bars_held" in trade
        assert "exit_reason" in trade

    def test_exit_reasons_valid(self, backtest_df, sample_config):
        preds = np.full(len(backtest_df), 2, dtype=int)
        trades = simulate_trades(preds, backtest_df, sample_config)
        valid_reasons = {"take_profit", "stop_loss", "timeout", "end_of_data"}
        for t in trades:
            assert t["exit_reason"] in valid_reasons

    def test_sl_checked_before_tp_same_bar(self, sample_config):
        """SL should be checked before TP on same bar (critical fix from NT8-RL)."""
        # Create data where both SL and TP would be hit on same bar
        n = 30
        close = np.full(n, 5200.0)
        high = close.copy()
        low = close.copy()

        # Bar 1: huge range that hits both SL and TP for a LONG
        tp_price = 5200.0 * (1 + 0.015)  # TP
        sl_price = 5200.0 * (1 - 0.010)  # SL
        high[1] = tp_price + 10  # TP would be hit
        low[1] = sl_price - 10   # SL would also be hit

        df = pd.DataFrame({
            "close": close, "high": high, "low": low,
            "open": close, "volume": np.full(n, 1000),
        })
        preds = np.full(n, 2, dtype=int)  # All LONG
        trades = simulate_trades(preds, df, sample_config)
        if trades:
            # SL should be checked first
            assert trades[0]["exit_reason"] == "stop_loss"


class TestComputeMetrics:
    """Test performance metrics computation."""

    def test_empty_trades(self):
        metrics = compute_metrics([])
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0
        assert metrics["sharpe"] == 0

    def test_all_wins(self):
        trades = [
            {"pnl_dollars": 500, "bars_held": 5, "exit_reason": "take_profit", "direction": "LONG"},
            {"pnl_dollars": 300, "bars_held": 8, "exit_reason": "take_profit", "direction": "LONG"},
        ]
        metrics = compute_metrics(trades)
        assert metrics["win_rate"] == 1.0
        assert metrics["total_pnl"] == 800

    def test_all_losses(self):
        trades = [
            {"pnl_dollars": -250, "bars_held": 3, "exit_reason": "stop_loss", "direction": "LONG"},
            {"pnl_dollars": -200, "bars_held": 4, "exit_reason": "stop_loss", "direction": "SHORT"},
        ]
        metrics = compute_metrics(trades)
        assert metrics["win_rate"] == 0
        assert metrics["total_pnl"] < 0

    def test_mixed_results(self, sample_trades):
        metrics = compute_metrics(sample_trades)
        assert metrics["total_trades"] == 5
        assert 0 < metrics["win_rate"] < 1
        assert metrics["avg_rr"] > 0
        assert metrics["profit_factor"] > 0

    def test_direction_breakdown(self, sample_trades):
        metrics = compute_metrics(sample_trades)
        assert metrics["long_trades"] == 3
        assert metrics["short_trades"] == 2

    def test_max_drawdown_positive(self, sample_trades):
        metrics = compute_metrics(sample_trades)
        assert metrics["max_drawdown"] >= 0


class TestMonteCarlo:
    """Test Monte Carlo simulation."""

    def test_empty_trades(self):
        result = monte_carlo([], 100)
        assert result["final_equity"]["P50"] == 0

    def test_returns_percentiles(self, sample_trades):
        result = monte_carlo(sample_trades, 100, seed=42)
        assert "P5" in result["final_equity"]
        assert "P50" in result["final_equity"]
        assert "P95" in result["final_equity"]
        assert "P5" in result["max_drawdown"]

    def test_p5_less_than_p95(self, sample_trades):
        result = monte_carlo(sample_trades, 100, seed=42)
        assert result["final_equity"]["P5"] <= result["final_equity"]["P95"]

    def test_reproducible_with_seed(self, sample_trades):
        r1 = monte_carlo(sample_trades, 100, seed=42)
        r2 = monte_carlo(sample_trades, 100, seed=42)
        assert r1["final_equity"]["P50"] == r2["final_equity"]["P50"]

    def test_iterations_count(self, sample_trades):
        result = monte_carlo(sample_trades, 200, seed=42)
        assert result["iterations"] == 200


class TestOverfittingScore:
    """Test overfitting detection."""

    def test_no_overfitting(self):
        result = overfitting_score(1.5, 1.5)
        assert result["score"] == pytest.approx(0, abs=0.01)
        assert result["assessment"] == "low"

    def test_severe_overfitting(self):
        result = overfitting_score(2.0, 0.2)
        assert result["score"] > 0.5
        assert result["assessment"] in ("high", "severe")

    def test_moderate_overfitting(self):
        result = overfitting_score(2.0, 1.2)
        assert result["assessment"] in ("low", "moderate")

    def test_zero_in_sample(self):
        result = overfitting_score(0, 1.0)
        assert result["assessment"] == "insufficient_data"

    def test_negative_in_sample(self):
        result = overfitting_score(-1.0, 0.5)
        assert result["assessment"] == "insufficient_data"

    def test_score_clamped_to_zero(self):
        # OOS > IS = negative overfitting -> should clamp to 0
        result = overfitting_score(1.0, 2.0)
        assert result["score"] == 0
