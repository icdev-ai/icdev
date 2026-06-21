# CUI // SP-CTI
"""Tests for tools/ai_augmentation/opportunity_scorer.py.

Covers:
  - IL penalty loading from config (not hardcoded)
  - detect_score_anomalies: value/feasibility delta flag
  - detect_score_anomalies: component outlier flags
  - score_opportunity: anomaly_flags included in result
"""
import pytest


def _import_scorer():
    from tools.ai_augmentation.opportunity_scorer import (
        _extract_components,
        _load_config,
        detect_score_anomalies,
        score_opportunity,
    )
    return _extract_components, _load_config, detect_score_anomalies, score_opportunity


# ============================================================================
# IL penalty tests — config-driven, not hardcoded
# ============================================================================

class TestILPenaltyFromConfig:
    def test_il4_no_penalty(self):
        _extract_components, _load_config, _, _ = _import_scorer()
        opp = {}  # no compliance_impact provided
        ctx = {"il_level": "il4"}
        result = _extract_components(opp, ctx)
        # compliance_impact defaults to 0.5, inversion → 0.5; il4 penalty = 0.0
        assert result["compliance_impact_inv"] == pytest.approx(0.5, abs=1e-6)

    def test_il5_applies_config_penalty(self):
        _extract_components, _load_config, _, _ = _import_scorer()
        # il5 should subtract 0.1 from the neutral 0.5
        opp = {}
        ctx = {"il_level": "il5"}
        result = _extract_components(opp, ctx)
        assert result["compliance_impact_inv"] == pytest.approx(0.4, abs=1e-6)

    def test_il6_applies_config_penalty(self):
        _extract_components, _load_config, _, _ = _import_scorer()
        opp = {}
        ctx = {"il_level": "il6"}
        result = _extract_components(opp, ctx)
        assert result["compliance_impact_inv"] == pytest.approx(0.3, abs=1e-6)

    def test_penalty_loaded_from_config_not_hardcoded(self):
        """Penalty dict must come from config; injecting a custom config overrides it."""
        _extract_components, _load_config, _, _ = _import_scorer()
        from tools.ai_augmentation.opportunity_scorer import _extract_components as ec_fn

        # Patch config with a different il5 penalty (0.20 instead of default 0.10)
        custom_cfg = {
            "il_penalties": {"il4": 0.0, "il5": 0.20, "il6": 0.30},
        }
        opp = {}
        ctx = {"il_level": "il5"}
        result = ec_fn(opp, ctx, config=custom_cfg)
        # With penalty = 0.20: 0.5 - 0.20 = 0.30
        assert result["compliance_impact_inv"] == pytest.approx(0.30, abs=1e-6)


# ============================================================================
# detect_score_anomalies tests
# ============================================================================

class TestDetectScoreAnomalies:
    def _make_score_result(
        self,
        value=0.6,
        feasibility=0.6,
        risk=0.5,
        composite=0.6,
        components=None,
    ):
        default_components = {
            "usage_freq_norm": 0.6,
            "task_complexity_norm": 0.6,
            "automation_deficit_norm": 0.6,
            "data_avail": 0.6,
            "il_model_exists": 0.6,
            "integration_complexity_norm": 0.4,
            "reversibility": 0.6,
            "compliance_impact_inv": 0.6,
            "dep_complexity_norm": 0.4,
        }
        return {
            "value_score": value,
            "feasibility_score": feasibility,
            "risk_score": risk,
            "composite_score": composite,
            "score_detail": {
                "components": components if components is not None else default_components,
            },
        }

    def test_no_anomalies_for_balanced_scores(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        result = self._make_score_result(value=0.6, feasibility=0.6)
        flags = detect_score_anomalies(result)
        assert flags == []

    def test_flags_large_value_feasibility_delta(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        # delta = |0.9 - 0.3| = 0.6 > default threshold 0.50
        result = self._make_score_result(value=0.9, feasibility=0.3)
        flags = detect_score_anomalies(result)
        types = [f["type"] for f in flags]
        assert "value_feasibility_delta" in types

    def test_no_flag_when_delta_equals_threshold(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        # delta = 0.50 is NOT > 0.50, so no flag
        result = self._make_score_result(value=0.9, feasibility=0.4)
        flags = detect_score_anomalies(result)
        delta_flags = [f for f in flags if f["type"] == "value_feasibility_delta"]
        assert delta_flags == []

    def test_flags_component_below_floor(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        components = {
            "usage_freq_norm": 0.02,  # below 0.05 floor
            "task_complexity_norm": 0.5,
            "automation_deficit_norm": 0.5,
            "data_avail": 0.5,
            "il_model_exists": 0.5,
            "integration_complexity_norm": 0.5,
            "reversibility": 0.5,
            "compliance_impact_inv": 0.5,
            "dep_complexity_norm": 0.5,
        }
        result = self._make_score_result(components=components)
        flags = detect_score_anomalies(result)
        low_flags = [f for f in flags if f["type"] == "component_outlier_low"]
        assert len(low_flags) == 1
        assert low_flags[0]["component"] == "usage_freq_norm"

    def test_flags_component_above_ceiling(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        components = {
            "usage_freq_norm": 0.5,
            "task_complexity_norm": 0.5,
            "automation_deficit_norm": 0.5,
            "data_avail": 0.97,  # above 0.95 ceiling
            "il_model_exists": 0.5,
            "integration_complexity_norm": 0.5,
            "reversibility": 0.5,
            "compliance_impact_inv": 0.5,
            "dep_complexity_norm": 0.5,
        }
        result = self._make_score_result(components=components)
        flags = detect_score_anomalies(result)
        high_flags = [f for f in flags if f["type"] == "component_outlier_high"]
        assert len(high_flags) == 1
        assert high_flags[0]["component"] == "data_avail"

    def test_custom_config_threshold_respected(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        custom_cfg = {
            "anomaly_detection": {
                "value_feasibility_max_delta": 0.20,
                "component_outlier_floor": 0.05,
                "component_outlier_ceiling": 0.95,
            }
        }
        # delta = 0.30 > 0.20 custom threshold
        result = self._make_score_result(value=0.7, feasibility=0.4)
        flags = detect_score_anomalies(result, config=custom_cfg)
        types = [f["type"] for f in flags]
        assert "value_feasibility_delta" in types

    def test_multiple_anomalies_returned(self):
        _, _, detect_score_anomalies, _ = _import_scorer()
        components = {
            "usage_freq_norm": 0.01,  # low outlier
            "task_complexity_norm": 0.5,
            "automation_deficit_norm": 0.5,
            "data_avail": 0.98,  # high outlier
            "il_model_exists": 0.5,
            "integration_complexity_norm": 0.5,
            "reversibility": 0.5,
            "compliance_impact_inv": 0.5,
            "dep_complexity_norm": 0.5,
        }
        # also large delta
        result = self._make_score_result(value=0.9, feasibility=0.2, components=components)
        flags = detect_score_anomalies(result)
        assert len(flags) >= 3


# ============================================================================
# score_opportunity: anomaly_flags in result
# ============================================================================

class TestScoreOpportunityAnomalyFlags:
    def test_anomaly_flags_key_present(self):
        _, _, _, score_opportunity = _import_scorer()
        opp = {
            "usage_freq": 0.7, "task_complexity": 0.6, "automation_deficit": 0.5,
            "data_avail": 0.8, "il_model_exists": 1, "integration_complexity": 0.3,
            "reversibility": 0.8, "compliance_impact": 0.2, "dep_complexity": 0.4,
        }
        result = score_opportunity(opp, {"il_level": "il4"})
        assert "anomaly_flags" in result
        assert isinstance(result["anomaly_flags"], list)

    def test_no_anomalies_for_normal_opportunity(self):
        _, _, _, score_opportunity = _import_scorer()
        opp = {
            "usage_freq": 0.6, "task_complexity": 0.6, "automation_deficit": 0.6,
            "data_avail": 0.6, "il_model_exists": 0.6, "integration_complexity": 0.4,
            "reversibility": 0.6, "compliance_impact": 0.4, "dep_complexity": 0.4,
        }
        result = score_opportunity(opp, {"il_level": "il4"})
        assert result["anomaly_flags"] == []
