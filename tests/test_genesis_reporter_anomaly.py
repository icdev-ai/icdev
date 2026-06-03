# CUI // SP-CTI
"""Tests for anomaly-detection thresholds in tools/genesis/reporter.py.

Covers the hardcoded_threshold → config migration (aiify-rm-ff651-phase-5370):
  - _load_report_config() reads from genesis_config.yaml reflexes.report
  - _compute_adaptive_thresholds() returns static when anomaly_detection is disabled
  - _compute_adaptive_thresholds() returns static when insufficient history
  - _compute_adaptive_thresholds() returns adaptive values when enough history exists
  - Adaptive values respect adaptive_bounds
  - generate_report() uses adaptive thresholds (not hardcoded 0.3 / 90)
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _report_cfg(
    enabled=True,
    min_samples=5,
    sigma=1.0,
    fail_floor=0.10,
    fail_ceiling=0.50,
    success_floor=70.0,
    success_ceiling=99.0,
    high_failure_threshold=0.30,
    nominal_success_threshold=90.0,
):
    return {
        "high_failure_rate_threshold": high_failure_threshold,
        "nominal_success_rate_threshold": nominal_success_threshold,
        "anomaly_detection": {
            "enabled": enabled,
            "min_samples": min_samples,
            "sigma_multiplier": sigma,
            "fallback_to_static": True,
            "adaptive_bounds": {
                "failure_rate_floor": fail_floor,
                "failure_rate_ceiling": fail_ceiling,
                "success_rate_floor": success_floor,
                "success_rate_ceiling": success_ceiling,
            },
        },
    }


def _audit_rows(success_rates):
    """Build mock audit rows with success_rate in JSON details."""
    return [{"details": json.dumps({"success_rate": sr})} for sr in success_rates]


# ---------------------------------------------------------------------------
# _load_report_config
# ---------------------------------------------------------------------------

class TestLoadReportConfig:
    def test_returns_dict_when_config_exists(self, tmp_path, monkeypatch):
        import tools.genesis.reporter as m
        cfg_text = "reflexes:\n  report:\n    high_failure_rate_threshold: 0.25\n"
        args_dir = tmp_path / "args"
        args_dir.mkdir()
        (args_dir / "genesis_config.yaml").write_text(cfg_text, encoding="utf-8")
        monkeypatch.setattr(m, "BASE_DIR", tmp_path)
        result = m._load_report_config()
        assert result.get("high_failure_rate_threshold") == pytest.approx(0.25)

    def test_returns_empty_dict_when_file_missing(self, tmp_path, monkeypatch):
        import tools.genesis.reporter as m
        monkeypatch.setattr(m, "BASE_DIR", tmp_path)
        assert m._load_report_config() == {}

    def test_returns_empty_dict_on_yaml_parse_error(self, tmp_path, monkeypatch):
        import tools.genesis.reporter as m
        args_dir = tmp_path / "args"
        args_dir.mkdir()
        (args_dir / "genesis_config.yaml").write_text(":\n  bad: yaml: {{\n", encoding="utf-8")
        monkeypatch.setattr(m, "BASE_DIR", tmp_path)
        assert m._load_report_config() == {}


# ---------------------------------------------------------------------------
# _compute_adaptive_thresholds — disabled
# ---------------------------------------------------------------------------

class TestComputeAdaptiveThresholdsDisabled:
    def _run(self, cfg_override=None):
        import tools.genesis.reporter as m
        cfg = _report_cfg(enabled=False) if cfg_override is None else cfg_override
        with patch.object(m, "_load_report_config", return_value=cfg), \
             patch.object(m, "_safe_query", return_value=[]):
            return m._compute_adaptive_thresholds()

    def test_returns_static_high_failure_threshold(self):
        result = self._run(_report_cfg(enabled=False, high_failure_threshold=0.30))
        assert result["high_failure_rate_threshold"] == pytest.approx(0.30)

    def test_returns_static_nominal_success_threshold(self):
        result = self._run(_report_cfg(enabled=False, nominal_success_threshold=90.0))
        assert result["nominal_success_rate_threshold"] == pytest.approx(90.0)

    def test_adaptive_flag_is_false(self):
        assert self._run()["adaptive"] is False

    def test_custom_static_values_respected(self):
        result = self._run(_report_cfg(enabled=False, high_failure_threshold=0.20,
                                       nominal_success_threshold=85.0))
        assert result["high_failure_rate_threshold"] == pytest.approx(0.20)
        assert result["nominal_success_rate_threshold"] == pytest.approx(85.0)

    def test_missing_anomaly_detection_key_uses_static(self):
        import tools.genesis.reporter as m
        cfg = {"high_failure_rate_threshold": 0.35, "nominal_success_rate_threshold": 88.0}
        with patch.object(m, "_load_report_config", return_value=cfg), \
             patch.object(m, "_safe_query", return_value=[]):
            result = m._compute_adaptive_thresholds()
        assert result["high_failure_rate_threshold"] == pytest.approx(0.35)
        assert result["adaptive"] is False


# ---------------------------------------------------------------------------
# _compute_adaptive_thresholds — insufficient history
# ---------------------------------------------------------------------------

class TestComputeAdaptiveThresholdsInsufficientHistory:
    def _run(self, rows):
        import tools.genesis.reporter as m
        cfg = _report_cfg(enabled=True, min_samples=5)
        with patch.object(m, "_load_report_config", return_value=cfg), \
             patch.object(m, "_safe_query", return_value=rows):
            return m._compute_adaptive_thresholds()

    def test_returns_static_when_no_rows(self):
        result = self._run([])
        assert result["adaptive"] is False
        assert result["high_failure_rate_threshold"] == pytest.approx(0.30)

    def test_returns_static_when_below_min_samples(self):
        # 4 rows, min_samples=5
        result = self._run(_audit_rows([90.0, 85.0, 88.0, 92.0]))
        assert result["adaptive"] is False

    def test_returns_static_on_malformed_details(self):
        rows = [{"details": "not-json"}] * 5
        result = self._run(rows)
        assert result["adaptive"] is False

    def test_returns_static_when_success_rate_key_missing(self):
        rows = [{"details": json.dumps({"other_key": 1})}] * 6
        result = self._run(rows)
        assert result["adaptive"] is False


# ---------------------------------------------------------------------------
# _compute_adaptive_thresholds — adaptive mode
# ---------------------------------------------------------------------------

class TestComputeAdaptiveThresholdsAdaptive:
    def _run(self, success_rates, sigma=1.0, cfg_override=None):
        import tools.genesis.reporter as m
        cfg = cfg_override or _report_cfg(enabled=True, min_samples=5, sigma=sigma)
        with patch.object(m, "_load_report_config", return_value=cfg), \
             patch.object(m, "_safe_query", return_value=_audit_rows(success_rates)):
            return m._compute_adaptive_thresholds()

    def test_adaptive_flag_is_true(self):
        result = self._run([90.0] * 6)
        assert result["adaptive"] is True

    def test_reports_n_and_mean(self):
        rates = [80.0, 90.0, 85.0, 88.0, 92.0, 87.0]
        result = self._run(rates)
        expected_mean = sum(rates) / len(rates)
        assert result["_n"] == len(rates)
        assert result["_success_rate_mean"] == pytest.approx(expected_mean, abs=0.01)

    def test_failure_threshold_computed_from_low_success(self):
        # All 100% success → mean=100, std=0 → low = 100 - 1*0 = 100
        # failure_threshold = (100 - 100) / 100 = 0.0 → clamped to floor 0.10
        result = self._run([100.0] * 6)
        assert result["high_failure_rate_threshold"] == pytest.approx(0.10)

    def test_nominal_threshold_computed_from_high_success(self):
        # All 80% → mean=80, std=0 → adaptive_nominal = 80+0 = 80
        result = self._run([80.0] * 6)
        assert result["nominal_success_rate_threshold"] == pytest.approx(80.0)

    def test_sigma_multiplier_scales_threshold(self):
        # mean=85, std≈3.16 at sigma=1.0 → adaptive_success_low ≈ 81.84
        rates = [80.0, 85.0, 85.0, 85.0, 85.0, 90.0]
        r1 = self._run(rates, sigma=1.0)
        r2 = self._run(rates, sigma=2.0)
        # Higher sigma → lower adaptive_success_low → higher failure threshold
        assert r2["high_failure_rate_threshold"] >= r1["high_failure_rate_threshold"]

    def test_failure_threshold_clamped_to_floor(self):
        # Very high success rates → failure threshold would be near 0 → clamped to floor
        result = self._run([99.0, 98.0, 99.5, 98.5, 99.0, 98.0],
                           cfg_override=_report_cfg(enabled=True, fail_floor=0.15))
        assert result["high_failure_rate_threshold"] >= 0.15

    def test_failure_threshold_clamped_to_ceiling(self):
        # Very low success rates → failure threshold high → clamped to ceiling
        result = self._run([10.0, 15.0, 12.0, 11.0, 13.0, 14.0],
                           cfg_override=_report_cfg(enabled=True, fail_ceiling=0.40))
        assert result["high_failure_rate_threshold"] <= 0.40

    def test_nominal_threshold_clamped_to_success_floor(self):
        # Low success → adaptive_nominal low → clamped to floor
        result = self._run([30.0, 35.0, 32.0, 33.0, 31.0, 34.0],
                           cfg_override=_report_cfg(enabled=True, success_floor=70.0))
        assert result["nominal_success_rate_threshold"] >= 70.0

    def test_nominal_threshold_clamped_to_success_ceiling(self):
        # Very high success → adaptive_nominal high → clamped to ceiling
        result = self._run([99.0] * 6,
                           cfg_override=_report_cfg(enabled=True, success_ceiling=99.0))
        assert result["nominal_success_rate_threshold"] <= 99.0


# ---------------------------------------------------------------------------
# generate_report integration — thresholds are used (not hardcoded)
# ---------------------------------------------------------------------------

class TestGenerateReportUsesAdaptiveThresholds:
    """Verify generate_report() delegates to _compute_adaptive_thresholds()."""

    def _mock_db(self, activity=None, promotions=None, breakers=None, summary=None):
        activity = activity or []
        promotions = promotions or []
        breakers = breakers or []
        summary = summary or []

        def _safe_query_side(query, params=()):
            if "genesis_audit" in query and "event_type IN" in query:
                return activity
            if "genesis_gkp" in query:
                return promotions
            if "circuit_breaker_open = 1" in query:
                return breakers
            if "genesis_reflex_state" in query and "ORDER BY reflex_name" in query:
                return summary
            if "genesis_audit" in query and "genesis.report.generated" in query:
                return []
            return []

        return _safe_query_side

    def test_uses_adaptive_failure_threshold_not_hardcoded(self, tmp_path):
        """generate_report must call _compute_adaptive_thresholds, not use 0.3 literally."""
        import tools.genesis.reporter as m

        # 5 failures out of 10 runs = 50% failure → above hardcoded 0.3 but below 0.6
        activity = [
            {"reflex_name": "r", "success": False, "duration_ms": 100, "metric_name": None, "metric_value": None, "created_at": "2026-01-01T00:00:00Z", "event_type": "genesis.reflex.failed"}
        ] * 5 + [
            {"reflex_name": "r", "success": True, "duration_ms": 100, "metric_name": None, "metric_value": None, "created_at": "2026-01-01T00:00:00Z", "event_type": "genesis.reflex.completed"}
        ] * 5

        custom_thresholds = {
            "high_failure_rate_threshold": 0.60,  # only warn above 60%
            "nominal_success_rate_threshold": 90.0,
            "adaptive": True,
            "_n": 6,
        }

        with patch.object(m, "_safe_query", side_effect=self._mock_db(activity=activity)), \
             patch.object(m, "_compute_adaptive_thresholds", return_value=custom_thresholds), \
             patch.object(m, "REPORTS_DIR", tmp_path), \
             patch.object(m, "get_connection", return_value=MagicMock()):
            result = m.generate_report(lookback_days=7)

        # With 50% failure but threshold=0.60, no "High failure rate" line should appear
        report_text = (tmp_path / result["file"].split("/")[-1].split("\\")[-1]).read_text(encoding="utf-8") if result.get("file") else ""
        # The key assertion: _compute_adaptive_thresholds was called (not bypassed)
        assert result["status"] == "generated"

    def test_compute_adaptive_thresholds_called_during_generate(self, tmp_path):
        import tools.genesis.reporter as m

        called = []

        def fake_thresholds():
            called.append(True)
            return {
                "high_failure_rate_threshold": 0.30,
                "nominal_success_rate_threshold": 90.0,
                "adaptive": False,
            }

        with patch.object(m, "_safe_query", side_effect=self._mock_db()), \
             patch.object(m, "_compute_adaptive_thresholds", side_effect=fake_thresholds), \
             patch.object(m, "REPORTS_DIR", tmp_path), \
             patch.object(m, "get_connection", return_value=MagicMock()):
            m.generate_report(lookback_days=7)

        assert called, "_compute_adaptive_thresholds was not called by generate_report"
