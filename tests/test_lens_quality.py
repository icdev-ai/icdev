# CUI // SP-CTI
"""Tests for Oracle Quality Lens adaptive anomaly detection."""
import pytest

from tools.oracle.lenses.lens_quality import (
    QualityLens,
    _MIN_SAMPLES,
    _Z_CRIT,
    _Z_CRIT_HIGH,
    _Z_CRIT_LOW,
    _Z_WARN,
    _Z_WARN_HIGH,
    _Z_WARN_LOW,
    _adaptive_z_thresholds,
    _anomaly_bounds,
)


class TestAdaptiveZThresholds:
    def test_stable_series_tighter_than_defaults(self):
        values = [100.0, 100.1, 100.0, 99.9, 100.0]
        z_warn, z_crit = _adaptive_z_thresholds(values)
        assert z_warn < _Z_WARN
        assert z_crit < _Z_CRIT

    def test_volatile_series_looser_than_defaults(self):
        values = [5.0, 95.0, 10.0, 90.0, 50.0]
        z_warn, z_crit = _adaptive_z_thresholds(values)
        assert z_warn > _Z_WARN
        assert z_crit > _Z_CRIT

    def test_insufficient_samples_returns_defaults(self):
        z_warn, z_crit = _adaptive_z_thresholds([1.0, 2.0])
        assert z_warn == _Z_WARN
        assert z_crit == _Z_CRIT

    def test_zero_mean_returns_defaults(self):
        z_warn, z_crit = _adaptive_z_thresholds([0.0, 0.0, 0.0])
        assert z_warn == _Z_WARN
        assert z_crit == _Z_CRIT

    def test_thresholds_stay_within_configured_range(self):
        test_cases = [
            [80.0] * 5,
            [5.0, 95.0, 10.0, 90.0, 50.0],
            [10.0, 20.0, 15.0, 12.0, 18.0],
        ]
        for values in test_cases:
            z_warn, z_crit = _adaptive_z_thresholds(values)
            assert _Z_WARN_LOW <= z_warn <= _Z_WARN_HIGH
            assert _Z_CRIT_LOW <= z_crit <= _Z_CRIT_HIGH

    def test_warn_always_less_than_crit(self):
        for values in [[1.0, 2.0, 3.0], [5.0, 50.0, 500.0, 5000.0, 1.0]]:
            z_warn, z_crit = _adaptive_z_thresholds(values)
            assert z_warn < z_crit


class TestAnomalyBounds:
    def test_too_few_samples_returns_none(self):
        assert _anomaly_bounds([1.0, 2.0]) is None

    def test_exact_min_samples_returns_dict(self):
        result = _anomaly_bounds([1.0, 2.0, 3.0])
        assert result is not None
        assert all(k in result for k in ("mean", "std", "upper_warn", "upper_crit", "lower_warn", "lower_crit"))

    def test_upper_crit_exceeds_upper_warn(self):
        result = _anomaly_bounds([10.0, 20.0, 30.0, 15.0, 25.0])
        assert result["upper_crit"] > result["upper_warn"]

    def test_lower_crit_below_lower_warn(self):
        result = _anomaly_bounds([10.0, 20.0, 30.0, 15.0, 25.0])
        assert result["lower_crit"] < result["lower_warn"]

    def test_explicit_z_overrides_adaptive(self):
        values = [10.0, 20.0, 30.0, 15.0, 25.0]
        fixed = _anomaly_bounds(values, z_warn=1.5, z_crit=2.5)
        assert fixed is not None
        std = fixed["std"]
        mean = fixed["mean"]
        assert abs(fixed["upper_warn"] - (mean + 1.5 * std)) < 1e-9

    def test_stable_series_tighter_upper_warn(self):
        stable = [100.0, 100.1, 100.0, 99.9, 100.0]
        adaptive = _anomaly_bounds(stable)
        fixed = _anomaly_bounds(stable, z_warn=_Z_WARN, z_crit=_Z_CRIT)
        assert adaptive["upper_warn"] < fixed["upper_warn"]


class TestQualityLensScore:
    def _empty(self):
        return {"uqs_history": [], "gate_history": [], "quality_snapshots": [], "recent_trends": []}

    def test_empty_analysis_no_predictions(self):
        assert QualityLens().score(self._empty()) == []

    def test_uqs_declining_trend_detected(self):
        analysis = {**self._empty(), "uqs_history": [
            {"uqs_score": 60}, {"uqs_score": 62}, {"uqs_score": 63},
            {"uqs_score": 90}, {"uqs_score": 92},
        ]}
        cats = [p.category for p in QualityLens().score(analysis)]
        assert "quality_regression" in cats

    def test_uqs_improving_trend_detected(self):
        analysis = {**self._empty(), "uqs_history": [
            {"uqs_score": 90}, {"uqs_score": 92}, {"uqs_score": 91},
            {"uqs_score": 60}, {"uqs_score": 62},
        ]}
        cats = [p.category for p in QualityLens().score(analysis)]
        assert "quality_improvement" in cats

    def test_stable_uqs_no_prediction(self):
        analysis = {**self._empty(), "uqs_history": [
            {"uqs_score": 80}, {"uqs_score": 81}, {"uqs_score": 79},
            {"uqs_score": 80}, {"uqs_score": 80},
        ]}
        cats = [p.category for p in QualityLens().score(analysis)]
        assert "quality_regression" not in cats
        assert "quality_improvement" not in cats

    def test_gate_failure_pattern_detected(self):
        analysis = {**self._empty(), "gate_history": [
            {"gate_id": "lint", "status": "fail"},
            {"gate_id": "lint", "status": "fail"},
            {"gate_id": "lint", "status": "pass"},
        ]}
        cats = [p.category for p in QualityLens().score(analysis)]
        assert "gate_failure_pattern" in cats

    def test_findings_spike_detected(self):
        # 6 stable baseline snapshots (~10 findings) + 1 clear spike at 100.
        # With a stable baseline, adaptive z-score stays near _Z_WARN_LOW so
        # the spike is well above the upper_warn threshold.
        analysis = {**self._empty(), "quality_snapshots": [
            {"total_findings": 100},
            {"total_findings": 10},
            {"total_findings": 11},
            {"total_findings": 10},
            {"total_findings": 9},
            {"total_findings": 10},
            {"total_findings": 11},
        ]}
        cats = [p.category for p in QualityLens().score(analysis)]
        assert "findings_spike" in cats

    def test_critical_trend_generates_prediction(self):
        analysis = {**self._empty(), "recent_trends": [
            {"severity": "critical", "trend_type": "regression", "detail": "failing tests"}
        ]}
        cats = [p.category for p in QualityLens().score(analysis)]
        assert "unresolved_trend" in cats

    def test_non_critical_trend_ignored(self):
        analysis = {**self._empty(), "recent_trends": [
            {"severity": "warning", "trend_type": "minor", "detail": "slight drift"}
        ]}
        assert QualityLens().score(analysis) == []

    def test_prediction_confidence_in_range(self):
        analysis = {**self._empty(), "uqs_history": [
            {"uqs_score": 60}, {"uqs_score": 62}, {"uqs_score": 63},
            {"uqs_score": 90}, {"uqs_score": 92},
        ]}
        for pred in QualityLens().score(analysis):
            assert 0.0 <= pred.confidence <= 1.0

    def test_gate_prediction_includes_rate_zscore(self):
        analysis = {**self._empty(), "gate_history": [
            {"gate_id": "security", "status": "fail"},
            {"gate_id": "security", "status": "fail"},
            {"gate_id": "security", "status": "pass"},
        ]}
        gate_preds = [p for p in QualityLens().score(analysis) if p.category == "gate_failure_pattern"]
        assert gate_preds
        assert "rate_zscore" in gate_preds[0].data
