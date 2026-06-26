# CUI // SP-CTI
"""Tests for configurable anomaly detection thresholds in tools/ai_augmentation/engine.py."""

from tools.ai_augmentation.engine import (
    _FALLBACK_ANOMALY_THRESHOLDS,
    _load_anomaly_thresholds,
)
from tools.aiify.engine import detect_score_anomalies


class TestFallbackThresholds:
    def test_all_required_keys_present(self):
        required = {
            "innovation_signal_min_score",
            "phase",
            "priority_high_min_score",
            "value_feasibility_max_delta",
            "component_outlier_floor",
            "component_outlier_ceiling",
        }
        assert required.issubset(set(_FALLBACK_ANOMALY_THRESHOLDS))

    def test_phase_sub_keys(self):
        phase = _FALLBACK_ANOMALY_THRESHOLDS["phase"]
        assert "p1_min_score" in phase
        assert "p2_min_score" in phase
        assert "p3_min_score" in phase

    def test_values_in_range(self):
        t = _FALLBACK_ANOMALY_THRESHOLDS
        assert 0.0 < t["innovation_signal_min_score"] <= 1.0
        assert 0.0 < t["priority_high_min_score"] <= 1.0
        assert 0.0 < t["value_feasibility_max_delta"] <= 1.0
        assert 0.0 <= t["component_outlier_floor"] < t["component_outlier_ceiling"] <= 1.0


class TestLoadAnomalyThresholds:
    def test_returns_dict(self):
        result = _load_anomaly_thresholds()
        assert isinstance(result, dict)

    def test_contains_phase_block(self):
        result = _load_anomaly_thresholds()
        assert "phase" in result

    def test_config_matches_fallback_keys(self):
        result = _load_anomaly_thresholds()
        for key in _FALLBACK_ANOMALY_THRESHOLDS:
            assert key in result, f"Missing key in loaded config: {key}"


class TestDetectScoreAnomalies:
    def _score(self, opp_id, v, f, r, composite=None):
        if composite is None:
            composite = v * 0.45 + f * 0.35 + (1 - r) * 0.20
        return {
            "opportunity_id": opp_id,
            "value_score": v,
            "feasibility_score": f,
            "risk_score": r,
            "composite_score": composite,
        }

    def test_empty_input_returns_empty(self):
        assert detect_score_anomalies([]) == []

    def test_no_anomalies_for_balanced_scores(self):
        rows = [self._score(1, 0.7, 0.65, 0.3)]
        anomalies = detect_score_anomalies(rows)
        assert anomalies == []

    def test_value_feasibility_delta_flagged(self):
        # v=0.9, f=0.2 → delta=0.7 > default 0.50
        rows = [self._score(1, 0.9, 0.2, 0.3)]
        anomalies = detect_score_anomalies(rows)
        reasons = [a["anomaly_type"] for a in anomalies]
        assert "value_feasibility_imbalance" in reasons

    def test_below_floor_flagged(self):
        # risk_score = 0.01 < floor 0.05
        rows = [self._score(2, 0.6, 0.6, 0.01)]
        anomalies = detect_score_anomalies(rows)
        reasons = [a["anomaly_type"] for a in anomalies]
        assert "component_outlier_low" in reasons

    def test_above_ceiling_flagged(self):
        # value_score = 0.99 > ceiling 0.95
        rows = [self._score(3, 0.99, 0.6, 0.3)]
        anomalies = detect_score_anomalies(rows)
        reasons = [a["anomaly_type"] for a in anomalies]
        assert "component_outlier_high" in reasons

    def test_custom_thresholds_respected(self):
        custom = {
            "value_feasibility_max_delta": 0.10,
            "component_outlier_floor": 0.20,
            "component_outlier_ceiling": 0.80,
        }
        # delta=0.15 > 0.10 custom threshold
        rows = [self._score(4, 0.60, 0.45, 0.3)]
        anomalies = detect_score_anomalies(rows, thresholds=custom)
        reasons = [a["anomaly_type"] for a in anomalies]
        assert "value_feasibility_imbalance" in reasons

    def test_anomaly_includes_opportunity_id(self):
        rows = [self._score(99, 0.9, 0.2, 0.3)]
        anomalies = detect_score_anomalies(rows)
        assert all(a["opportunity_id"] == 99 for a in anomalies)

    def test_multiple_anomalies_per_row(self):
        # v=0.99 (above ceiling), f=0.01 (below floor), delta=0.98 (exceeds max delta)
        rows = [self._score(5, 0.99, 0.01, 0.3)]
        anomalies = detect_score_anomalies(rows)
        assert len(anomalies) >= 2
