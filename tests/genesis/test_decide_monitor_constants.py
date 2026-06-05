# CUI // SP-CTI
"""Tests for constant extraction in decide.py and monitor.py (proposal_genesis reflexes)."""
import sys
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.decide import (
    BID_THRESHOLD, NO_BID_THRESHOLD, SCORE_WEIGHTS,
    _BAYESIAN_MIN_SAMPLES, _WEIGHT_CLAMP_MIN, _WEIGHT_CLAMP_MAX,
    _INFO_GAIN_PS_WEIGHT, _INFO_GAIN_DISC_WEIGHT, _DISCRIMINABILITY_MULT,
    _CALIBRATION_MIN_OUTCOMES, _GAP_POSITIVE_SHIFT,
    _COMP_TEAMING_WEIGHT, _COMP_ENGAGEMENT_WEIGHT,
    _COMPLIANCE_BASELINE, _SETASIDE_BONUS, _NAICS_BONUS,
    _RESOURCE_BASELINE, _RESOURCE_WITH_PLAN,
    _STRATEGIC_BASELINE, _STRATEGIC_VALUE_HIGH, _STRATEGIC_VALUE_MED,
    _EARLY_TERM_DIMS, _EARLY_TERM_MULTIPLIER,
    _STRENGTH_THRESHOLD, _WEAKNESS_THRESHOLD,
    _QUALITY_DENOMINATOR, _MAX_DECISIONS_PER_RUN,
)
from tools.proposal_genesis.reflexes.monitor import (
    _CPI_CRITICAL, _CPI_WARNING, _CPI_INFO,
    _SPI_CRITICAL, _SPI_WARNING, _SPI_INFO, _TCPI_WARNING,
    _OVERDUE_CRITICAL_DAYS, _OVERDUE_WARNING_DAYS, _OVERDUE_PENALTY_CAP,
    _UPCOMING_SURGE_COUNT, _UPCOMING_WINDOW_DAYS,
    _SEVERITY_WEIGHTS, _CPARS_W_EVM, _CPARS_W_SCHEDULE, _CPARS_W_RISK,
    _CPARS_W_SB, _CPARS_W_TREND,
    _CPARS_EXCEPTIONAL, _CPARS_VERY_GOOD, _CPARS_SATISFACTORY, _CPARS_MARGINAL,
    _HEALTH_GREEN, _HEALTH_YELLOW,
    _HEALTH_W_EVM, _HEALTH_W_SCHEDULE, _HEALTH_W_RISK, _HEALTH_W_SB, _HEALTH_W_TREND,
    _assess_evm_health, _assess_risk_health, _assess_schedule_health,
    _predict_cpars, _compute_contract_health,
)


# ─────────────────────────────────────────────────────────────────
# decide.py constants
# ─────────────────────────────────────────────────────────────────

class TestDecideConstants:
    def test_decision_thresholds_ordered(self):
        assert BID_THRESHOLD > NO_BID_THRESHOLD > 0

    def test_score_weights_sum_to_one(self):
        assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 0.001

    def test_bayesian_params(self):
        assert _BAYESIAN_MIN_SAMPLES > 0
        assert 0.0 < _WEIGHT_CLAMP_MIN < _WEIGHT_CLAMP_MAX < 1.0

    def test_info_gain_weights_sum_to_one(self):
        assert abs(_INFO_GAIN_PS_WEIGHT + _INFO_GAIN_DISC_WEIGHT - 1.0) < 0.001

    def test_discriminability_mult_positive(self):
        assert _DISCRIMINABILITY_MULT > 0

    def test_calibration_min_outcomes(self):
        assert _CALIBRATION_MIN_OUTCOMES > 0
        assert 0.0 < _GAP_POSITIVE_SHIFT < 1.0

    def test_competitive_blend_weights_sum_to_one(self):
        assert abs(_COMP_TEAMING_WEIGHT + _COMP_ENGAGEMENT_WEIGHT - 1.0) < 0.001

    def test_scoring_baselines_in_range(self):
        for v in (_COMPLIANCE_BASELINE, _RESOURCE_BASELINE, _RESOURCE_WITH_PLAN, _STRATEGIC_BASELINE):
            assert 0.0 <= v <= 1.0

    def test_value_thresholds_ordered(self):
        assert _STRATEGIC_VALUE_HIGH > _STRATEGIC_VALUE_MED > 0

    def test_early_term_params(self):
        assert _EARLY_TERM_DIMS > 0
        assert 0.0 < _EARLY_TERM_MULTIPLIER <= 1.0

    def test_strength_weakness_ordered(self):
        assert _STRENGTH_THRESHOLD > _WEAKNESS_THRESHOLD > 0

    def test_misc_constants(self):
        assert _QUALITY_DENOMINATOR > 0
        assert _MAX_DECISIONS_PER_RUN > 0
        assert _SETASIDE_BONUS > 0
        assert _NAICS_BONUS > 0


# ─────────────────────────────────────────────────────────────────
# monitor.py constants + behavior
# ─────────────────────────────────────────────────────────────────

class TestMonitorConstants:
    def test_cpi_thresholds_ordered(self):
        assert _CPI_CRITICAL < _CPI_WARNING < _CPI_INFO

    def test_spi_thresholds_ordered(self):
        assert _SPI_CRITICAL < _SPI_WARNING < _SPI_INFO

    def test_tcpi_above_one(self):
        assert _TCPI_WARNING > 1.0

    def test_overdue_days_ordered(self):
        assert _OVERDUE_CRITICAL_DAYS > _OVERDUE_WARNING_DAYS > 0

    def test_penalty_cap_valid(self):
        assert 0.0 < _OVERDUE_PENALTY_CAP <= 1.0

    def test_upcoming_params(self):
        assert _UPCOMING_SURGE_COUNT > 0
        assert _UPCOMING_WINDOW_DAYS > 0

    def test_severity_weights_ordered(self):
        assert _SEVERITY_WEIGHTS["critical"] > _SEVERITY_WEIGHTS["high"] > _SEVERITY_WEIGHTS["medium"] > _SEVERITY_WEIGHTS["low"]

    def test_cpars_weights_sum_to_one(self):
        total = _CPARS_W_EVM + _CPARS_W_SCHEDULE + _CPARS_W_RISK + _CPARS_W_SB + _CPARS_W_TREND
        assert abs(total - 1.0) < 0.001

    def test_cpars_bands_ordered(self):
        assert _CPARS_EXCEPTIONAL > _CPARS_VERY_GOOD > _CPARS_SATISFACTORY > _CPARS_MARGINAL > 0

    def test_health_weights_sum_to_one(self):
        total = _HEALTH_W_EVM + _HEALTH_W_SCHEDULE + _HEALTH_W_RISK + _HEALTH_W_SB + _HEALTH_W_TREND
        assert abs(total - 1.0) < 0.001

    def test_health_bands_ordered(self):
        assert _HEALTH_GREEN > _HEALTH_YELLOW > 0


class TestMonitorBehavior:
    def test_evm_critical_cpi_penalized(self):
        r = _assess_evm_health({"cpi": 0.70, "spi": 1.0})
        assert any(a["level"] == "critical" and a["metric"] == "CPI" for a in r["alerts"])
        assert r["score"] < 1.0

    def test_evm_healthy_no_alerts(self):
        r = _assess_evm_health({"cpi": 1.0, "spi": 1.0, "tcpi": 1.0})
        assert r["score"] == 1.0
        assert r["alerts"] == []

    def test_evm_empty_returns_perfect(self):
        r = _assess_evm_health({})
        assert r["score"] == 1.0

    def test_risk_health_severity_penalty(self):
        r = _assess_risk_health([{"severity": "critical", "event_type": "stop_work", "description": "x"}])
        assert r["score"] == pytest.approx(1.0 - _SEVERITY_WEIGHTS["critical"], abs=0.001)

    def test_schedule_overdue_penalized(self):
        r = _assess_schedule_health(
            overdue=[{"id": "d1", "title": "CDRL-1", "days_overdue": 45}],
            upcoming=[],
        )
        assert r["score"] < 1.0
        assert any(a["level"] == "critical" for a in r["alerts"])

    def test_predict_cpars_exceptional(self):
        # All healthy → high CPARS
        healthy = {"score": 1.0}
        r = _predict_cpars("c1", healthy, healthy, healthy)
        assert r["predicted_rating"] in ("exceptional", "very_good")
        assert 1.0 <= r["predicted_score"] <= 5.0

    def test_compute_health_green(self):
        healthy = {"score": 1.0}
        r = _compute_contract_health(healthy, healthy, healthy)
        assert r["health"] == "green"
        assert r["health_score"] >= _HEALTH_GREEN

    def test_compute_health_red(self):
        unhealthy = {"score": 0.0}
        r = _compute_contract_health(unhealthy, unhealthy, unhealthy)
        # sb + trend placeholders contribute 0.20, so score = 0.20 < yellow band
        assert r["health"] == "red"
