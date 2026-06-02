"""Tests for anomaly-detection and config-driven thresholds in genesis/reflexes/evolve.py."""
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.evolve import (
    _compute_anomaly_thresholds,
    _compute_confidence,
)


# ---------------------------------------------------------------------------
# _compute_anomaly_thresholds — config-driven parameters
# ---------------------------------------------------------------------------


def _mock_conn(n, mean_smells, var_smells, mean_maint, var_maint):
    """Return a mock DB connection whose execute().fetchone() returns a tuple matching the SQL column order.

    SQL column order: mean_smells(0), var_smells(1), mean_maint(2), var_maint(3), n(4)
    """
    row = (mean_smells, var_smells, mean_maint, var_maint, n)
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    return conn


class TestComputeAnomalyThresholds:
    def test_uses_config_sigma_multiplier(self):
        """A larger sigma_multiplier produces a wider threshold gap."""
        mean_s, std_s = 5.0, 2.0
        mean_m, std_m = 60.0, 10.0

        anomaly_cfg_narrow = {"sigma_multiplier": 0.5, "min_samples": 5}
        anomaly_cfg_wide = {"sigma_multiplier": 2.0, "min_samples": 5}

        conn = _mock_conn(20, mean_s, std_s ** 2, mean_m, std_m ** 2)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result_narrow = _compute_anomaly_thresholds(anomaly_cfg_narrow)

        conn2 = _mock_conn(20, mean_s, std_s ** 2, mean_m, std_m ** 2)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn2):
            result_wide = _compute_anomaly_thresholds(anomaly_cfg_wide)

        # Wide sigma → higher smell threshold, lower maint threshold
        assert result_wide["smell_threshold"] > result_narrow["smell_threshold"]
        assert result_wide["maintainability_threshold"] < result_narrow["maintainability_threshold"]

    def test_uses_config_min_samples(self):
        """When n < min_samples, fallback values should be used instead of adaptive."""
        # 5 samples available; min_samples = 10 → should fall back
        conn = _mock_conn(5, 4.0, 1.0, 55.0, 9.0)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds({"min_samples": 10})

        assert result["computed"] is False

    def test_uses_config_min_samples_adaptive_when_enough(self):
        """When n >= min_samples, adaptive mode should activate."""
        # 5 samples available; min_samples = 5 → should compute adaptive
        conn = _mock_conn(5, 4.0, 1.0, 55.0, 9.0)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds({"min_samples": 5, "sigma_multiplier": 0.5})

        assert result["computed"] is True

    def test_uses_config_fallback_smell_threshold(self):
        """Custom fallback_smell_threshold is returned when data is insufficient."""
        conn = _mock_conn(2, 0.0, 0.0, 0.0, 0.0)
        custom_fallback = 3.5
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds(
                {"min_samples": 10, "fallback_smell_threshold": custom_fallback}
            )

        assert result["smell_threshold"] == custom_fallback
        assert result["computed"] is False

    def test_uses_config_fallback_maintainability_threshold(self):
        """Custom fallback_maintainability_threshold is returned when data is insufficient."""
        conn = _mock_conn(2, 0.0, 0.0, 0.0, 0.0)
        custom_fallback = 75.0
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds(
                {"min_samples": 10, "fallback_maintainability_threshold": custom_fallback}
            )

        assert result["maintainability_threshold"] == custom_fallback

    def test_uses_config_smell_threshold_floor(self):
        """Adaptive smell threshold never drops below smell_threshold_floor."""
        # mean=0.2, std=0.1 → mean + 0.5*std = 0.25, but floor=2.0 → result=2.0
        conn = _mock_conn(20, 0.2, 0.01, 80.0, 25.0)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds(
                {
                    "min_samples": 10,
                    "sigma_multiplier": 0.5,
                    "adaptive_bounds": {"smell_threshold_floor": 2.0, "maint_threshold_floor": 0.0},
                }
            )

        assert result["smell_threshold"] >= 2.0

    def test_uses_config_maint_threshold_floor(self):
        """Adaptive maint threshold never drops below maint_threshold_floor."""
        # mean=90.0, std=1.0 → maint threshold = 90 - 0.5*1 = 89.5, but if floor=40.0, still 89.5
        # If mean=30.0, std=5.0 → maint threshold = 30 - 0.5*5 = 27.5, but floor=40.0 → result=40.0
        conn = _mock_conn(20, 8.0, 4.0, 30.0, 25.0)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds(
                {
                    "min_samples": 10,
                    "sigma_multiplier": 0.5,
                    "adaptive_bounds": {"smell_threshold_floor": 1.0, "maint_threshold_floor": 40.0},
                }
            )

        assert result["maintainability_threshold"] >= 40.0

    def test_db_error_returns_fallback(self):
        """DB errors return config-specified fallback values."""
        with patch(
            "tools.genesis.reflexes.evolve.get_connection", side_effect=Exception("db fail")
        ):
            result = _compute_anomaly_thresholds(
                {"fallback_smell_threshold": 2.0, "fallback_maintainability_threshold": 45.0}
            )

        assert result["smell_threshold"] == 2.0
        assert result["maintainability_threshold"] == 45.0
        assert result["computed"] is False

    def test_default_config_matches_original_constants(self):
        """With no config override, defaults match original hardcoded values (1.0, 50.0)."""
        conn = _mock_conn(2, 0.0, 0.0, 0.0, 0.0)
        with patch("tools.genesis.reflexes.evolve.get_connection", return_value=conn):
            result = _compute_anomaly_thresholds()

        assert result["smell_threshold"] == 1.0
        assert result["maintainability_threshold"] == 50.0


# ---------------------------------------------------------------------------
# _compute_confidence — config-driven confidence tiers
# ---------------------------------------------------------------------------


class TestComputeConfidence:
    def test_tests_failed_returns_config_value(self):
        """tests_failed confidence reads from tiers_cfg."""
        conf, rationale = _compute_confidence(
            "low", False, False, True,
            tiers_cfg={"tests_failed": 0.20}
        )
        assert conf == 0.20
        assert rationale == "tests_failed"

    def test_tests_failed_default_value(self):
        """tests_failed default is 0.35 when no config provided."""
        conf, _ = _compute_confidence("low", False, False, True)
        assert conf == 0.35

    def test_sandbox_penalty_from_config(self):
        """sandbox_penalty reads from tiers_cfg."""
        conf_no_pen, _ = _compute_confidence("low", True, True, True)
        conf_with_pen, _ = _compute_confidence(
            "low", True, True, False,
            tiers_cfg={"sandbox_penalty": 0.20}
        )
        assert conf_no_pen - conf_with_pen == pytest.approx(0.20, abs=1e-9)

    def test_sandbox_penalty_default(self):
        """Default sandbox_penalty is 0.10."""
        conf_clean, _ = _compute_confidence("low", True, True, True)
        conf_dirty, _ = _compute_confidence("low", True, True, False)
        assert conf_clean - conf_dirty == pytest.approx(0.10, abs=1e-9)

    def test_low_risk_metrics_improved_from_config(self):
        conf, rationale = _compute_confidence(
            "low", True, True, True,
            tiers_cfg={"low_risk_improved": 0.90}
        )
        assert conf == 0.90
        assert "low_risk" in rationale

    def test_low_risk_from_config(self):
        conf, rationale = _compute_confidence(
            "low", True, False, True,
            tiers_cfg={"low_risk": 0.80}
        )
        assert conf == 0.80

    def test_medium_risk_metrics_improved_from_config(self):
        conf, rationale = _compute_confidence(
            "medium", True, True, True,
            tiers_cfg={"medium_risk_improved": 0.70}
        )
        assert conf == 0.70

    def test_medium_risk_from_config(self):
        conf, rationale = _compute_confidence(
            "medium", True, False, True,
            tiers_cfg={"medium_risk": 0.55}
        )
        assert conf == 0.55

    def test_high_risk_from_config(self):
        conf, rationale = _compute_confidence(
            "high", True, False, True,
            tiers_cfg={"high_risk": 0.30}
        )
        assert conf == 0.30

    def test_default_tiers_match_original_hardcoded(self):
        """All default tiers must match the original hardcoded constants."""
        cases = [
            ("low", True, True, True, 0.75),
            ("low", True, False, True, 0.65),
            ("medium", True, True, True, 0.60),
            ("medium", True, False, True, 0.50),
            ("high", True, False, True, 0.45),
        ]
        for risk, tp, mi, sb, expected in cases:
            conf, _ = _compute_confidence(risk, tp, mi, sb)
            assert conf == pytest.approx(expected), f"risk={risk}, tp={tp}, mi={mi}"

    def test_config_overrides_do_not_affect_other_tiers(self):
        """Overriding one tier does not bleed into others."""
        cfg = {"low_risk_improved": 0.99}
        # medium tier should still use its default
        conf_med, _ = _compute_confidence("medium", True, False, True, tiers_cfg=cfg)
        assert conf_med == pytest.approx(0.50)

    def test_sandbox_penalty_applied_with_config_tier(self):
        """Sandbox penalty applies on top of config-specified tier value."""
        cfg = {"low_risk_improved": 0.80, "sandbox_penalty": 0.15}
        conf_sandbox_fail, _ = _compute_confidence("low", True, True, False, tiers_cfg=cfg)
        assert conf_sandbox_fail == pytest.approx(0.80 - 0.15)
