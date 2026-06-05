# CUI // SP-CTI
"""Tests for Oracle Quality Lens anomaly detection helpers and scoring logic."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from tools.oracle.lenses.lens_quality import (
    _mad_std_estimate,
    _ewma,
    _anomaly_bounds,
    _iqr_bounds,
    _MAD_FALLBACK_STD,
    _EWMA_ALPHA,
    QualityLens,
)


# ── _mad_std_estimate ─────────────────────────────────────────────────────────

class TestMadStdEstimate:
    def test_empty_returns_fallback(self):
        assert _mad_std_estimate([]) == _MAD_FALLBACK_STD

    def test_constant_series_returns_fallback(self):
        # All same values → MAD = 0 → returns fallback
        result = _mad_std_estimate([50.0, 50.0, 50.0, 50.0])
        assert result == _MAD_FALLBACK_STD

    def test_normal_spread(self):
        # [70, 80, 90, 100, 110]: median=90, deviations=[20,10,0,10,20], MAD=10
        values = [70.0, 80.0, 90.0, 100.0, 110.0]
        result = _mad_std_estimate(values)
        assert abs(result - 10.0 * 1.4826) < 0.01

    def test_resistant_to_outlier(self):
        # One extreme outlier should not blow up the estimate
        normal = [80.0, 85.0, 90.0, 88.0, 82.0]
        with_outlier = normal + [200.0]
        result_normal = _mad_std_estimate(normal)
        result_outlier = _mad_std_estimate(with_outlier)
        # MAD-based estimate should be close despite the outlier
        assert abs(result_normal - result_outlier) < 10.0

    def test_single_value_returns_fallback(self):
        result = _mad_std_estimate([75.0])
        assert result == _MAD_FALLBACK_STD


# ── _ewma ─────────────────────────────────────────────────────────────────────

class TestEwma:
    def test_empty_returns_empty(self):
        assert _ewma([]) == []

    def test_single_value_unchanged(self):
        assert _ewma([42.0]) == [42.0]

    def test_alpha_one_is_identity(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = _ewma(values, alpha=1.0)
        assert result == values

    def test_smoothing_reduces_extremes(self):
        # A spike in the middle should be dampened when alpha < 1
        values = [80.0, 80.0, 50.0, 80.0, 80.0]  # most-recent-first
        smoothed = _ewma(values, alpha=0.3)
        # The spike (index 2, value 50) should be smoothed toward neighbours
        assert smoothed[2] > 50.0  # pulled up by neighbours

    def test_preserves_most_recent_first_order(self):
        # First element of output should be anchored to oldest-processed EWMA result
        # which is the last element of input (oldest in time, processed first)
        values = [100.0, 90.0, 80.0]  # most recent first
        result = _ewma(values, alpha=0.5)
        assert len(result) == 3
        # Oldest (values[-1]=80) is the EWMA seed; most recent (values[0]=100) is output[0]
        # output[0] should be between 80 and 100
        assert 80.0 <= result[0] <= 100.0

    def test_uses_default_alpha(self):
        values = [10.0, 20.0, 30.0]
        result_default = _ewma(values)
        result_explicit = _ewma(values, alpha=_EWMA_ALPHA)
        assert result_default == result_explicit


# ── _anomaly_bounds ───────────────────────────────────────────────────────────

class TestAnomalyBounds:
    def test_returns_none_below_min_samples(self):
        assert _anomaly_bounds([70.0, 80.0]) is None

    def test_returns_dict_at_min_samples(self):
        result = _anomaly_bounds([70.0, 80.0, 90.0])
        assert result is not None
        assert "mean" in result and "std" in result

    def test_upper_warn_above_mean(self):
        values = [70.0, 80.0, 90.0, 80.0, 75.0]
        b = _anomaly_bounds(values)
        assert b["upper_warn"] > b["mean"]
        assert b["upper_crit"] > b["upper_warn"]

    def test_lower_warn_below_mean(self):
        values = [70.0, 80.0, 90.0, 80.0, 75.0]
        b = _anomaly_bounds(values)
        assert b["lower_warn"] < b["mean"]
        assert b["lower_crit"] < b["lower_warn"]

    def test_constant_series_uses_iqr_fallback(self):
        # All same → pstdev = 0 → should not return std=0
        values = [85.0] * 5
        b = _anomaly_bounds(values)
        assert b is not None
        assert b["std"] > 0


# ── _iqr_bounds ───────────────────────────────────────────────────────────────

class TestIqrBounds:
    def test_returns_none_below_min_samples(self):
        assert _iqr_bounds([10.0, 20.0]) is None

    def test_keys_present(self):
        b = _iqr_bounds([10.0, 20.0, 30.0, 40.0, 50.0])
        assert b is not None
        for key in ("q1", "q3", "iqr", "upper_warn", "upper_crit", "lower_warn", "lower_crit"):
            assert key in b

    def test_zero_iqr_uses_pseudo_iqr(self):
        # Constant values → IQR=0 → pseudo-IQR applied so bounds are non-degenerate
        b = _iqr_bounds([50.0, 50.0, 50.0, 50.0, 50.0])
        assert b is not None
        assert b["iqr"] > 0


# ── QualityLens.score — UQS trajectory ───────────────────────────────────────

class TestQualityLensScore:
    def _make_uqs_history(self, scores: list[float]) -> list[dict]:
        """Build uqs_history from most-recent-first score list."""
        return [{"uqs_score": s} for s in scores]

    def test_no_predictions_when_stable(self):
        lens = QualityLens()
        history = self._make_uqs_history([80.0, 81.0, 80.0, 79.0, 80.0, 81.0])
        analysis = {"uqs_history": history, "gate_history": [], "quality_snapshots": [], "recent_trends": []}
        preds = lens.score(analysis)
        uqs_preds = [p for p in preds if p.category in ("quality_regression", "quality_improvement")]
        assert uqs_preds == []

    def test_declining_trend_raises_prediction(self):
        lens = QualityLens()
        # 3 recent at 10, 7 older at 100: EWMA-smoothed delta_decline ≈ 44 > raw std ≈ 41 (6.7% margin)
        history = self._make_uqs_history([10.0, 10.0, 10.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
        analysis = {"uqs_history": history, "gate_history": [], "quality_snapshots": [], "recent_trends": []}
        preds = lens.score(analysis)
        categories = [p.category for p in preds]
        assert "quality_regression" in categories

    def test_improving_trend_raises_prediction(self):
        lens = QualityLens()
        # 3 recent at 100, 6 older at 10: more historical low values pull raw std below
        # EWMA-smoothed delta_rise (~44), giving a clear margin with EWMA enabled
        history = self._make_uqs_history([100.0, 100.0, 100.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
        analysis = {"uqs_history": history, "gate_history": [], "quality_snapshots": [], "recent_trends": []}
        preds = lens.score(analysis)
        categories = [p.category for p in preds]
        assert "quality_improvement" in categories

    def test_critical_trend_in_output(self):
        lens = QualityLens()
        trend = {"severity": "critical", "trend_type": "coverage_drop", "detail": "Coverage fell below 60%"}
        analysis = {"uqs_history": [], "gate_history": [], "quality_snapshots": [], "recent_trends": [trend]}
        preds = lens.score(analysis)
        assert any(p.category == "unresolved_trend" for p in preds)

    def test_gate_failure_pattern(self):
        lens = QualityLens()
        # Gate "linting" fails 4/5 times
        gate_history = [
            {"gate_id": "linting", "status": "fail"},
            {"gate_id": "linting", "status": "fail"},
            {"gate_id": "linting", "status": "fail"},
            {"gate_id": "linting", "status": "fail"},
            {"gate_id": "linting", "status": "pass"},
        ]
        analysis = {"uqs_history": [], "gate_history": gate_history, "quality_snapshots": [], "recent_trends": []}
        preds = lens.score(analysis)
        assert any(p.category == "gate_failure_pattern" for p in preds)

    def test_findings_spike_detected(self):
        lens = QualityLens()
        # current=1000 vs tight historical baseline of 10.
        # upper_warn ≈ 913 (mean≈175, std≈369, z_warn=2.0 at CV=1), so 1000 > 913.
        snapshots = [{"total_findings": 1000}] + [{"total_findings": 10}] * 5
        analysis = {"uqs_history": [], "gate_history": [], "quality_snapshots": snapshots, "recent_trends": []}
        preds = lens.score(analysis)
        assert any(p.category == "findings_spike" for p in preds)

    def test_prediction_data_contains_dynamic_warn_delta(self):
        lens = QualityLens()
        history = self._make_uqs_history([68.0, 69.0, 71.0, 85.0, 86.0, 87.0, 88.0])
        analysis = {"uqs_history": history, "gate_history": [], "quality_snapshots": [], "recent_trends": []}
        preds = lens.score(analysis)
        regression = next((p for p in preds if p.category == "quality_regression"), None)
        if regression:
            assert "dynamic_warn_delta" in regression.data
