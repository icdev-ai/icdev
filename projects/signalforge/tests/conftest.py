#!/usr/bin/env python3
"""Shared test fixtures for SignalForge tests."""
# CUI // SP-CTI

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def sample_ohlcv():
    """Generate sample OHLCV data for testing (500 bars)."""
    np.random.seed(42)
    n = 500
    base_price = 5200.0

    # Random walk for close prices
    returns = np.random.normal(0, 0.001, n)
    close = base_price * np.cumprod(1 + returns)

    # Generate OHLCV from close
    high = close * (1 + np.abs(np.random.normal(0, 0.002, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.002, n)))
    open_ = close * (1 + np.random.normal(0, 0.001, n))
    volume = np.random.randint(100, 5000, n)

    # Generate RTH timestamps (9:30-16:00, 1-min bars)
    timestamps = pd.date_range("2024-06-03 09:30", periods=n, freq="min")

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
    return df


@pytest.fixture
def sample_config():
    """Standard trading config for tests."""
    return {
        "features": {
            "sma_periods": [20, 50, 200],
            "rsi_period": 14,
            "macd": [12, 26, 9],
            "bb_period": 20,
            "bb_std": 2,
            "atr_period": 14,
            "adx_period": 14,
            "stoch": [14, 3, 3],
            "volume_sma_period": 20,
            "support_resistance_lookback": 50,
        },
        "model": {
            "type": "xgboost",
            "lookahead_bars": 20,
            "min_move_threshold": 0.001,
            "max_depth": 3,
            "n_estimators": 10,  # Small for fast tests
            "learning_rate": 0.1,
            "min_child_weight": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "eval_metric": "mlogloss",
            "early_stopping_rounds": 5,
            "cv_folds": 2,
        },
        "risk": {
            "take_profit_pct": 0.015,
            "stop_loss_pct": 0.010,
            "max_daily_loss_pct": 0.03,
            "max_positions": 1,
            "position_size_method": "atr",
            "risk_per_trade_pct": 0.01,
            "trailing_stop": {
                "enabled": True,
                "atr_multiplier": 2.0,
                "activate_pct": 0.005,
            },
            "end_of_day_flatten": True,
        },
        "backtest": {
            "walk_forward_train_months": 6,
            "walk_forward_test_months": 1,
            "walk_forward_step_months": 1,
            "monte_carlo_iterations": 100,  # Small for fast tests
            "min_trades_for_validity": 5,  # Low for test data
            "commission_per_side": 2.50,
            "slippage_ticks": 1,
        },
    }


@pytest.fixture
def tmp_db(tmp_path):
    """Temporary database path for trade journal tests."""
    return str(tmp_path / "test_signalforge.db")
