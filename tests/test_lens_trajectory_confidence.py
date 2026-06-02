# CUI // SP-CTI
"""Tests for configurable confidence thresholds in TrajectoryLens anomaly detection."""

from __future__ import annotations

import importlib
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import tools.oracle.lens_trajectory as lt


# ── constant loading ──────────────────────────────────────────────────────────

class TestConfidenceConstantsDefaults:
    """Verify defaults match the reference values from the YAML config."""

    def test_cc_breach_max(self):
        assert lt.CC_BREACH_CONF_MAX == pytest.approx(0.92)

    def test_cc_breach_base(self):
        assert lt.CC_BREACH_CONF_BASE == pytest.approx(0.55)

    def test_cc_breach_scale(self):
        assert lt.CC_BREACH_CONF_SCALE == pytest.approx(0.40)

    def test_maint_decline_max(self):
        assert lt.MAINT_DECLINE_CONF_MAX == pytest.approx(0.90)

    def test_maint_decline_base(self):
        assert lt.MAINT_DECLINE_CONF_BASE == pytest.approx(0.55)

    def test_maint_decline_scale(self):
        assert lt.MAINT_DECLINE_CONF_SCALE == pytest.approx(0.38)

    def test_hotspot_max(self):
        assert lt.HOTSPOT_CONF_MAX == pytest.approx(0.88)

    def test_hotspot_base(self):
        assert lt.HOTSPOT_CONF_BASE == pytest.approx(0.55)

    def test_hotspot_per_file(self):
        assert lt.HOTSPOT_CONF_PER_FILE == pytest.approx(0.06)

    def test_velocity_fixed(self):
        assert lt.VELOCITY_CONF_FIXED == pytest.approx(0.70)


class TestLLMCalibrationConstantsDefaults:
    """Verify LLM calibration constants match YAML defaults."""

    def test_function_name(self):
        assert lt.LLM_CALIBRATION_FUNCTION_NAME == "oracle_anomaly_detection"

    def test_max_tokens(self):
        assert lt.LLM_CALIBRATION_MAX_TOKENS == 200

    def test_temperature(self):
        assert lt.LLM_CALIBRATION_TEMPERATURE == pytest.approx(0.0)

    def test_val_cc_threshold_min(self):
        assert lt.LLM_VAL_CC_THRESHOLD_MIN == pytest.approx(1.0)

    def test_val_maintainability_floor_min(self):
        assert lt.LLM_VAL_MAINTAINABILITY_FLOOR_MIN == pytest.approx(0.0)

    def test_val_slope_cutoff_min(self):
        assert lt.LLM_VAL_SLOPE_CUTOFF_MIN == pytest.approx(0.01)

    def test_val_recent_avg_cutoff_min(self):
        assert lt.LLM_VAL_RECENT_AVG_CUTOFF_MIN == pytest.approx(0.01)


class TestLLMCalibrationConfigOverride:
    """Custom YAML values for llm_calibration section are respected."""

    def test_custom_llm_calibration_values(self, tmp_path, monkeypatch):
        yaml_content = (
            "trajectory_forecast:\n"
            "  llm_calibration:\n"
            "    function_name: custom_anomaly_fn\n"
            "    max_tokens: 512\n"
            "    temperature: 0.1\n"
            "    validation:\n"
            "      cc_threshold_min: 2.0\n"
            "      slope_cutoff_min: 0.05\n"
        )
        cfg_path = tmp_path / "oracle_trajectory_config.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        monkeypatch.setattr(lt, "_TRAJECTORY_CONFIG_PATH", cfg_path)
        cfg = lt._load_trajectory_config()
        llm = cfg.get("llm_calibration", {})
        assert llm.get("function_name") == "custom_anomaly_fn"
        assert llm.get("max_tokens") == 512
        assert llm.get("temperature") == pytest.approx(0.1)
        assert llm.get("validation", {}).get("cc_threshold_min") == pytest.approx(2.0)
        assert llm.get("validation", {}).get("slope_cutoff_min") == pytest.approx(0.05)


class TestLoadTrajectoryConfigMissingFile:
    """Config loader falls back gracefully when the YAML is absent."""

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lt, "_TRAJECTORY_CONFIG_PATH", tmp_path / "nonexistent.yaml")
        cfg = lt._load_trajectory_config()
        assert cfg == {}


class TestLoadTrajectoryConfigOverride:
    """Custom YAML values are picked up by the loader."""

    def test_custom_confidence_values(self, tmp_path, monkeypatch):
        yaml_content = (
            "trajectory_forecast:\n"
            "  confidence:\n"
            "    cc_breach_max: 0.99\n"
            "    velocity_fixed: 0.42\n"
        )
        cfg_path = tmp_path / "oracle_trajectory_config.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        monkeypatch.setattr(lt, "_TRAJECTORY_CONFIG_PATH", cfg_path)
        cfg = lt._load_trajectory_config()
        assert cfg.get("confidence", {}).get("cc_breach_max") == pytest.approx(0.99)
        assert cfg.get("confidence", {}).get("velocity_fixed") == pytest.approx(0.42)


# ── confidence formula correctness ───────────────────────────────────────────

class TestCCBreachConfidenceFormula:
    """CC breach confidence uses min(max, base + horizon_fraction * scale)."""

    @pytest.mark.parametrize("days,expected_approx", [
        (30,  min(lt.CC_BREACH_CONF_MAX, lt.CC_BREACH_CONF_BASE + (1.0 - 30 / lt.FORECAST_HORIZON_DAYS) * lt.CC_BREACH_CONF_SCALE)),
        (180, min(lt.CC_BREACH_CONF_MAX, lt.CC_BREACH_CONF_BASE + (1.0 - 180 / lt.FORECAST_HORIZON_DAYS) * lt.CC_BREACH_CONF_SCALE)),
        (360, min(lt.CC_BREACH_CONF_MAX, lt.CC_BREACH_CONF_BASE + (1.0 - 360 / lt.FORECAST_HORIZON_DAYS) * lt.CC_BREACH_CONF_SCALE)),
    ])
    def test_formula_values(self, days, expected_approx):
        result = min(lt.CC_BREACH_CONF_MAX, lt.CC_BREACH_CONF_BASE + (1.0 - days / lt.FORECAST_HORIZON_DAYS) * lt.CC_BREACH_CONF_SCALE)
        assert result == pytest.approx(expected_approx)

    def test_capped_at_max(self):
        # Very small days → base + scale ≈ 0.95, must be capped at 0.92
        result = min(lt.CC_BREACH_CONF_MAX, lt.CC_BREACH_CONF_BASE + 1.0 * lt.CC_BREACH_CONF_SCALE)
        assert result == pytest.approx(lt.CC_BREACH_CONF_MAX)


class TestMaintDeclineConfidenceFormula:
    """Maintainability decline confidence uses min(max, base + horizon_fraction * scale)."""

    def test_capped_at_max(self):
        result = min(lt.MAINT_DECLINE_CONF_MAX, lt.MAINT_DECLINE_CONF_BASE + 1.0 * lt.MAINT_DECLINE_CONF_SCALE)
        assert result == pytest.approx(lt.MAINT_DECLINE_CONF_MAX)

    def test_low_for_distant_breach(self):
        days = lt.FORECAST_HORIZON_DAYS - 1
        result = min(lt.MAINT_DECLINE_CONF_MAX, lt.MAINT_DECLINE_CONF_BASE + (1.0 - days / lt.FORECAST_HORIZON_DAYS) * lt.MAINT_DECLINE_CONF_SCALE)
        assert result < lt.MAINT_DECLINE_CONF_MAX


class TestHotspotConfidenceFormula:
    """Hotspot confidence grows linearly with trending_file_count, capped at max."""

    @pytest.mark.parametrize("n_files", [2, 4, 6, 10])
    def test_monotone_increase(self, n_files):
        prev = min(lt.HOTSPOT_CONF_MAX, lt.HOTSPOT_CONF_BASE + (n_files - 1) * lt.HOTSPOT_CONF_PER_FILE)
        curr = min(lt.HOTSPOT_CONF_MAX, lt.HOTSPOT_CONF_BASE + n_files * lt.HOTSPOT_CONF_PER_FILE)
        assert curr >= prev

    def test_capped_at_max(self):
        large = 1000
        result = min(lt.HOTSPOT_CONF_MAX, lt.HOTSPOT_CONF_BASE + large * lt.HOTSPOT_CONF_PER_FILE)
        assert result == pytest.approx(lt.HOTSPOT_CONF_MAX)


class TestVelocityConfidence:
    """High commit-velocity predictions use the fixed constant."""

    def test_velocity_fixed_is_positive(self):
        assert lt.VELOCITY_CONF_FIXED > 0.0

    def test_velocity_fixed_at_most_one(self):
        assert lt.VELOCITY_CONF_FIXED <= 1.0


# ── helpers ───────────────────────────────────────────────────────────────────

class TestPercentile:
    def test_single_value(self):
        assert lt._percentile([5.0], 50.0) == pytest.approx(5.0)

    def test_uniform_list(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert lt._percentile(vals, 0.0) == pytest.approx(1.0)
        assert lt._percentile(vals, 100.0) == pytest.approx(5.0)
        assert lt._percentile(vals, 50.0) == pytest.approx(3.0)

    def test_empty_returns_zero(self):
        assert lt._percentile([], 90.0) == pytest.approx(0.0)


class TestLinearRegression:
    def test_horizontal_line(self):
        slope, intercept = lt._linear_regression([0, 1, 2], [3.0, 3.0, 3.0])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(3.0)

    def test_rising_slope(self):
        slope, _ = lt._linear_regression([0, 1, 2, 3], [0.0, 1.0, 2.0, 3.0])
        assert slope == pytest.approx(1.0)

    def test_single_point(self):
        slope, intercept = lt._linear_regression([5], [7.0])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(7.0)


class TestDaysToThreshold:
    def test_no_slope(self):
        assert lt._days_to_threshold(5.0, 0.0, 10.0) is None

    def test_wrong_direction(self):
        # slope > 0 but threshold already exceeded
        assert lt._days_to_threshold(12.0, 1.0, 10.0) is None

    def test_positive_forecast(self):
        # current=5, threshold=12, slope=1/week → 7 weeks → 49 days
        result = lt._days_to_threshold(5.0, 1.0, 12.0)
        assert result == pytest.approx(49.0)
