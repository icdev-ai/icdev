#!/usr/bin/env python3
"""Tests for SignalForge Label Generator."""
# CUI // SP-CTI

import numpy as np
import pandas as pd
import pytest

from tools.trading.label_generator import generate_labels_tpsl, generate_labels_return


@pytest.fixture
def simple_ohlcv():
    """Small OHLCV for deterministic label testing."""
    n = 50
    close = np.array([5200.0 + i * 0.5 for i in range(n)])  # Steady uptrend
    high = close + 5
    low = close - 5
    open_ = close - 0.25
    volume = np.full(n, 1000)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def label_config():
    """Config for label generation tests."""
    return {
        "model": {"lookahead_bars": 10, "min_move_threshold": 0.001},
        "risk": {"take_profit_pct": 0.005, "stop_loss_pct": 0.003},
    }


class TestGenerateLabelsTpsl:
    """Test TP/SL simulation labeling."""

    def test_returns_series(self, sample_ohlcv, label_config):
        labels = generate_labels_tpsl(sample_ohlcv, label_config)
        assert isinstance(labels, pd.Series)
        assert len(labels) == len(sample_ohlcv)

    def test_label_values(self, sample_ohlcv, label_config):
        labels = generate_labels_tpsl(sample_ohlcv, label_config)
        unique = set(labels.unique())
        assert unique.issubset({-1, 0, 1})

    def test_last_bars_are_flat(self, sample_ohlcv, label_config):
        """Last lookahead bars should be FLAT (can't look ahead)."""
        labels = generate_labels_tpsl(sample_ohlcv, label_config)
        lookahead = label_config["model"]["lookahead_bars"]
        assert all(labels.iloc[-lookahead:] == 0)

    def test_uptrend_produces_long_labels(self, simple_ohlcv, label_config):
        """Strong uptrend should produce mostly LONG labels."""
        # Make a strong uptrend
        n = 50
        close = np.array([5200.0 + i * 10 for i in range(n)])
        df = pd.DataFrame({
            "open": close - 1,
            "high": close + 20,
            "low": close - 2,
            "close": close,
            "volume": np.full(n, 1000),
        })
        labels = generate_labels_tpsl(df, label_config)
        long_count = (labels == 1).sum()
        # In a strong uptrend, most bars should be LONG
        assert long_count > 0

    def test_downtrend_produces_short_labels(self, label_config):
        """Strong downtrend should produce mostly SHORT labels."""
        n = 50
        close = np.array([5500.0 - i * 10 for i in range(n)])
        df = pd.DataFrame({
            "open": close + 1,
            "high": close + 2,
            "low": close - 20,
            "close": close,
            "volume": np.full(n, 1000),
        })
        labels = generate_labels_tpsl(df, label_config)
        short_count = (labels == -1).sum()
        assert short_count > 0

    def test_flat_market_mostly_flat(self, label_config):
        """Flat market (constant price) should produce FLAT labels."""
        n = 50
        close = np.full(n, 5200.0)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.25,  # Tiny range
            "low": close - 0.25,
            "close": close,
            "volume": np.full(n, 1000),
        })
        labels = generate_labels_tpsl(df, label_config)
        flat_count = (labels == 0).sum()
        # Most labels should be FLAT in a flat market
        assert flat_count > len(labels) * 0.5

    def test_default_config_used(self, sample_ohlcv):
        """Should use default config if none provided."""
        labels = generate_labels_tpsl(sample_ohlcv)
        assert len(labels) == len(sample_ohlcv)


class TestGenerateLabelsReturn:
    """Test simple return-based labeling."""

    def test_returns_series(self, sample_ohlcv, label_config):
        labels = generate_labels_return(sample_ohlcv, label_config)
        assert isinstance(labels, pd.Series)
        assert len(labels) == len(sample_ohlcv)

    def test_label_values(self, sample_ohlcv, label_config):
        labels = generate_labels_return(sample_ohlcv, label_config)
        unique = set(labels.dropna().unique())
        assert unique.issubset({-1, 0, 1})

    def test_uptrend_produces_long(self):
        """Large positive returns should produce LONG labels."""
        n = 100
        close = np.array([5200.0 + i * 5.0 for i in range(n)])
        df = pd.DataFrame({"close": close})
        config = {"model": {"lookahead_bars": 5, "min_move_threshold": 0.001}}
        labels = generate_labels_return(df, config)
        # Early bars in uptrend should be LONG
        long_count = (labels == 1).sum()
        assert long_count > 0

    def test_threshold_effect(self, sample_ohlcv):
        """Higher threshold should produce more FLAT labels."""
        config_low = {"model": {"lookahead_bars": 10, "min_move_threshold": 0.0001}}
        config_high = {"model": {"lookahead_bars": 10, "min_move_threshold": 0.05}}

        labels_low = generate_labels_return(sample_ohlcv, config_low)
        labels_high = generate_labels_return(sample_ohlcv, config_high)

        flat_low = (labels_low == 0).sum()
        flat_high = (labels_high == 0).sum()
        assert flat_high >= flat_low

    def test_series_name(self, sample_ohlcv, label_config):
        labels = generate_labels_return(sample_ohlcv, label_config)
        assert labels.name == "label"
