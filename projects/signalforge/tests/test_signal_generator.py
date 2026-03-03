#!/usr/bin/env python3
"""Tests for SignalForge Signal Generator."""
# CUI // SP-CTI

import numpy as np
import pandas as pd
import pytest

from tools.trading.signal_generator import generate_signal, LABEL_MAP_INV


class MockModel:
    """Mock XGBoost model for testing."""

    def __init__(self, prediction=1, probabilities=None):
        self._prediction = prediction
        self._probabilities = probabilities or [0.2, 0.6, 0.2]

    def predict(self, X):
        return np.array([self._prediction])

    def predict_proba(self, X):
        return np.array([self._probabilities])


@pytest.fixture
def feature_cols():
    return ["close", "high", "low", "volume", "sma_20", "rsi_14",
            "macd_line", "atr_14", "regime"]


@pytest.fixture
def feature_row():
    """Single row of features."""
    return pd.DataFrame({
        "close": [5200.0],
        "high": [5205.0],
        "low": [5195.0],
        "volume": [1500],
        "sma_20": [0.01],
        "rsi_14": [0.55],
        "macd_line": [0.001],
        "atr_14": [0.002],
        "regime": [1],
    })


class TestGenerateSignal:
    """Test signal generation from model predictions."""

    def test_long_signal(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=2, probabilities=[0.1, 0.2, 0.7])
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        assert result["signal"] == "LONG"
        assert result["signal_value"] == 1
        assert result["confidence"] == pytest.approx(0.7, abs=0.01)

    def test_short_signal(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=0, probabilities=[0.7, 0.2, 0.1])
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        assert result["signal"] == "SHORT"
        assert result["signal_value"] == -1

    def test_flat_signal(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=1, probabilities=[0.2, 0.6, 0.2])
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        assert result["signal"] == "FLAT"
        assert result["signal_value"] == 0

    def test_tp_sl_levels_long(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=2)
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        assert result["take_profit"] > result["entry_price"]
        assert result["stop_loss"] < result["entry_price"]

    def test_tp_sl_levels_short(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=0)
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        assert result["take_profit"] < result["entry_price"]
        assert result["stop_loss"] > result["entry_price"]

    def test_tp_sl_levels_flat(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=1)
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        # FLAT: TP and SL both equal entry
        assert result["take_profit"] == result["entry_price"]
        assert result["stop_loss"] == result["entry_price"]

    def test_class_probabilities(self, feature_row, feature_cols, sample_config):
        model = MockModel(prediction=2, probabilities=[0.15, 0.25, 0.60])
        result = generate_signal(model, feature_row, feature_cols, sample_config)
        probs = result["class_probabilities"]
        assert "SHORT" in probs
        assert "FLAT" in probs
        assert "LONG" in probs
        assert sum(probs.values()) == pytest.approx(1.0, abs=0.01)

    def test_missing_features_handled(self, feature_cols, sample_config):
        """Should handle missing feature columns gracefully."""
        df = pd.DataFrame({"close": [5200.0], "volume": [1000]})
        model = MockModel(prediction=1)
        result = generate_signal(model, df, feature_cols, sample_config)
        assert result["signal"] in ("LONG", "SHORT", "FLAT")

    def test_inf_values_cleaned(self, feature_cols, sample_config):
        """Should handle inf values in features."""
        df = pd.DataFrame({
            "close": [5200.0],
            "high": [np.inf],
            "low": [-np.inf],
            "volume": [1000],
            "sma_20": [np.nan],
            "rsi_14": [0.5],
            "macd_line": [0.0],
            "atr_14": [0.002],
            "regime": [0],
        })
        model = MockModel(prediction=1)
        result = generate_signal(model, df, feature_cols, sample_config)
        assert result["signal"] in ("LONG", "SHORT", "FLAT")


class TestLabelMapInv:
    """Test label mapping constants."""

    def test_all_classes_mapped(self):
        assert LABEL_MAP_INV[0] == "SHORT"
        assert LABEL_MAP_INV[1] == "FLAT"
        assert LABEL_MAP_INV[2] == "LONG"
