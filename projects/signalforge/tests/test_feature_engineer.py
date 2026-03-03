#!/usr/bin/env python3
"""Tests for SignalForge Feature Engineering."""
# CUI // SP-CTI

import numpy as np
import pandas as pd
import pytest

from tools.trading.feature_engineer import (
    compute_features, load_ohlcv, filter_rth,
    _sma, _ema, _rsi, _macd, _bollinger_bands, _atr,
)


class TestHelperFunctions:
    """Test individual indicator functions."""

    def test_sma_basic(self):
        s = pd.Series([1, 2, 3, 4, 5], dtype=float)
        result = _sma(s, 3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_ema_basic(self):
        s = pd.Series([1, 2, 3, 4, 5], dtype=float)
        result = _ema(s, 3)
        assert len(result) == 5
        # EMA should be close to recent values
        assert result.iloc[-1] > result.iloc[0]

    def test_rsi_range(self):
        np.random.seed(42)
        close = pd.Series(100 + np.cumsum(np.random.normal(0, 1, 100)))
        result = _rsi(close, 14)
        valid = result.dropna()
        assert all(valid >= 0)
        assert all(valid <= 100)

    def test_macd_components(self):
        np.random.seed(42)
        close = pd.Series(100 + np.cumsum(np.random.normal(0, 0.5, 100)))
        line, signal, hist = _macd(close, 12, 26, 9)
        assert len(line) == 100
        assert len(signal) == 100
        # Hist = line - signal
        valid_idx = hist.dropna().index
        for idx in valid_idx[:5]:
            assert hist[idx] == pytest.approx(line[idx] - signal[idx], abs=1e-10)

    def test_bollinger_bands_symmetric(self):
        close = pd.Series([100.0] * 30)
        upper, lower = _bollinger_bands(close, 20, 2)
        # With constant prices, std=0, bands collapse to SMA
        assert upper.iloc[-1] == pytest.approx(100.0)
        assert lower.iloc[-1] == pytest.approx(100.0)

    def test_atr_positive(self, sample_ohlcv):
        result = _atr(sample_ohlcv["high"], sample_ohlcv["low"],
                      sample_ohlcv["close"], 14)
        valid = result.dropna()
        assert all(valid > 0)


class TestComputeFeatures:
    """Test the main feature computation function."""

    def test_output_shape(self, sample_ohlcv, sample_config):
        features = compute_features(sample_ohlcv, sample_config.get("features"))
        assert len(features) == len(sample_ohlcv)
        # Should have 25 features + timestamp
        feature_cols = [c for c in features.columns if c != "timestamp"]
        assert len(feature_cols) == 25

    def test_expected_columns(self, sample_ohlcv, sample_config):
        features = compute_features(sample_ohlcv, sample_config.get("features"))
        expected = [
            "close", "high", "low", "volume",
            "sma_20", "sma_50", "sma_200",
            "rsi_14", "macd_line", "macd_signal", "macd_hist",
            "bb_upper_dist", "bb_lower_dist",
            "atr_14", "adx_14", "stoch_k", "stoch_d",
            "volume_sma_ratio", "dist_to_support", "dist_to_resistance",
            "regime", "hour_of_day", "day_of_week",
            "close_vs_vwap", "bar_range_vs_atr",
        ]
        for col in expected:
            assert col in features.columns, f"Missing column: {col}"

    def test_no_inf_values(self, sample_ohlcv, sample_config):
        features = compute_features(sample_ohlcv, sample_config.get("features"))
        clean = features.dropna()
        numeric = clean.select_dtypes(include=[np.number])
        assert not np.isinf(numeric.values).any(), "Features contain inf values"

    def test_rsi_normalized(self, sample_ohlcv, sample_config):
        features = compute_features(sample_ohlcv, sample_config.get("features"))
        valid = features["rsi_14"].dropna()
        assert all(valid >= 0), "RSI below 0"
        assert all(valid <= 1), "RSI above 1 (should be normalized)"

    def test_regime_values(self, sample_ohlcv, sample_config):
        features = compute_features(sample_ohlcv, sample_config.get("features"))
        valid = features["regime"].dropna()
        assert set(valid.unique()).issubset({-1, 0, 1})

    def test_timestamp_preserved(self, sample_ohlcv, sample_config):
        features = compute_features(sample_ohlcv, sample_config.get("features"))
        assert "timestamp" in features.columns


class TestLoadOhlcv:
    """Test OHLCV data loading."""

    def test_load_standard_format(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        df = pd.DataFrame({
            "timestamp": ["2024-01-02 09:30", "2024-01-02 09:31"],
            "open": [5200.0, 5201.0],
            "high": [5205.0, 5206.0],
            "low": [5198.0, 5199.0],
            "close": [5202.0, 5203.0],
            "volume": [100, 200],
        })
        df.to_csv(csv_path, index=False)
        result = load_ohlcv(csv_path)
        assert len(result) == 2
        assert "close" in result.columns

    def test_load_alternative_column_names(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        df = pd.DataFrame({
            "Date": ["2024-01-02"],
            "Open": [5200.0],
            "High": [5205.0],
            "Low": [5198.0],
            "Close": [5202.0],
            "Volume": [100],
        })
        df.to_csv(csv_path, index=False)
        result = load_ohlcv(csv_path)
        assert "close" in result.columns

    def test_missing_columns_raises(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        df = pd.DataFrame({"foo": [1], "bar": [2]})
        df.to_csv(csv_path, index=False)
        with pytest.raises(ValueError, match="Missing columns"):
            load_ohlcv(csv_path)


class TestFilterRth:
    """Test RTH filtering."""

    def test_filters_premarket(self):
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([
                "2024-01-02 08:00", "2024-01-02 09:30",
                "2024-01-02 12:00", "2024-01-02 16:30",
            ]),
            "open": [1, 2, 3, 4],
            "high": [1, 2, 3, 4],
            "low": [1, 2, 3, 4],
            "close": [1, 2, 3, 4],
            "volume": [1, 2, 3, 4],
        })
        result = filter_rth(df)
        assert len(result) == 2  # Only 09:30 and 12:00

    def test_no_timestamp_returns_all(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = filter_rth(df)
        assert len(result) == 3
