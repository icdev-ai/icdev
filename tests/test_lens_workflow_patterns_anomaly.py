# CUI // SP-CTI
"""Tests for extended LLM-driven anomaly threshold calibration in WorkflowPatternLens.

Covers:
  - _llm_anomaly_thresholds: extended key set (z_critical, z_warning, adaptive_z_*,
    min_self_heal_fails, heal_fail_distribution param)
  - _calibrate_anomaly_thresholds: passes heal distribution to LLM function
  - Scoring methods: use calibrated Z multipliers / severity boundaries
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.oracle.lenses.lens_workflow_patterns import (
    WorkflowPatternLens,
    _llm_anomaly_thresholds,
    _z_score_severity,
    _ADAPTIVE_Z_HEAL,
    _HEAL_REFLEX_MIN_RATE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lens() -> WorkflowPatternLens:
    return WorkflowPatternLens()


def _mock_llm_response(payload: dict) -> MagicMock:
    """Build a mock LLMRouter that returns payload as JSON."""
    resp = MagicMock()
    resp.content = json.dumps(payload)
    router = MagicMock()
    router.invoke.return_value = resp
    return router


# ---------------------------------------------------------------------------
# _llm_anomaly_thresholds — extended key set
# ---------------------------------------------------------------------------

_LLM_ROUTER_PATH = "tools.llm.router.LLMRouter"
_LLM_REQUEST_PATH = "tools.llm.provider.LLMRequest"


class TestLLMAnomalyThresholdsExtended:
    """LLM calibration now returns extended keys beyond the original 4."""

    FULL_PAYLOAD = {
        "min_pattern_freq": 5,
        "cooccurrence_threshold": 0.85,
        "fallback_freq_denominator": 25.0,
        "fallback_session_denominator": 12.0,
        "z_critical": 3.5,
        "z_warning": 2.5,
        "adaptive_z_ngram": 1.5,
        "adaptive_z_cooccurrence": 1.2,
        "adaptive_z_kanban": 0.8,
        "min_self_heal_fails": 3.0,
        "adaptive_z_heal": 0.5,
        "heal_reflex_min_rate": 0.6,
    }

    def test_extended_keys_parsed(self):
        router = _mock_llm_response(self.FULL_PAYLOAD)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([10.0, 5.0, 3.0], [0.8, 0.9], heal_distribution=[2.0, 3.0, 5.0])

        assert result["z_critical"] == pytest.approx(3.5)
        assert result["z_warning"] == pytest.approx(2.5)
        assert result["adaptive_z_ngram"] == pytest.approx(1.5)
        assert result["adaptive_z_cooccurrence"] == pytest.approx(1.2)
        assert result["adaptive_z_kanban"] == pytest.approx(0.8)
        assert result["min_self_heal_fails"] == pytest.approx(3.0)

    def test_original_keys_still_present(self):
        router = _mock_llm_response(self.FULL_PAYLOAD)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([10.0, 5.0], [0.8, 0.9])

        assert result["min_pattern_freq"] == pytest.approx(5.0)
        assert result["cooccurrence_threshold"] == pytest.approx(0.85)

    def test_z_critical_clamped_above_minimum(self):
        payload = dict(self.FULL_PAYLOAD, z_critical=0.5)  # below min
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([10.0, 5.0], [0.8])

        # z_critical must be >= 1.0
        assert result["z_critical"] >= 1.0

    def test_z_warning_clamped_above_minimum(self):
        payload = dict(self.FULL_PAYLOAD, z_warning=0.1)  # below min
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([10.0, 5.0], [0.8])

        assert result["z_warning"] >= 0.5

    def test_adaptive_z_clamped_non_negative(self):
        payload = dict(self.FULL_PAYLOAD, adaptive_z_ngram=-1.0)
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([10.0, 5.0], [0.8])

        assert result.get("adaptive_z_ngram", 0.0) >= 0.0

    def test_min_self_heal_fails_clamped_above_one(self):
        payload = dict(self.FULL_PAYLOAD, min_self_heal_fails=0.2)
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([10.0], [0.8], heal_distribution=[1.0, 2.0])

        assert result.get("min_self_heal_fails", 1.0) >= 1.0

    def test_returns_empty_on_llm_failure(self):
        bad_router = MagicMock()
        bad_router.invoke.side_effect = RuntimeError("LLM unavailable")
        with patch(_LLM_ROUTER_PATH, return_value=bad_router):
            result = _llm_anomaly_thresholds([10.0, 5.0], [0.8])

        assert result == {}

    def test_returns_empty_on_empty_inputs(self):
        result = _llm_anomaly_thresholds([], [])
        assert result == {}

    def test_heal_distribution_included_in_prompt(self):
        """The LLM router must be called with a prompt referencing heal data."""
        req_messages: list = []

        class CapturingRequest:
            def __init__(self, messages, **kw):
                req_messages.extend(messages)
                self.messages = messages
                self.__dict__.update(kw)

        with patch(_LLM_ROUTER_PATH, return_value=_mock_llm_response(self.FULL_PAYLOAD)):
            with patch("tools.llm.provider.LLMRequest", CapturingRequest):
                _llm_anomaly_thresholds([10.0, 5.0], [0.8], heal_distribution=[2.0, 4.0, 6.0])

        user_msgs = [m for m in req_messages if m.get("role") == "user"]
        assert user_msgs, "No user messages captured"
        user_content = user_msgs[0]["content"]
        assert (
            "heal_fail" in user_content.lower()
            or "self_heal" in user_content.lower()
            or "self-heal" in user_content.lower()
        )


# ---------------------------------------------------------------------------
# _calibrate_anomaly_thresholds — heal distribution forwarded
# ---------------------------------------------------------------------------

class TestCalibrateAnomalyThresholds:
    """_calibrate_anomaly_thresholds must forward the self-healing fail distribution."""

    def _analysis_with_heal_candidates(self) -> dict:
        sessions = {
            "s1": ["code_generated", "test_executed", "test_passed"],
            "s2": ["code_generated", "security_scan", "test_passed"],
            "s3": ["code_generated", "test_executed", "test_passed"],
        }
        return {
            "sessions": sessions,
            "failed_then_done": [
                {"actor": "bot", "action": "deploy", "fail_count": 3, "success_count": 2, "heal_rate": 0.4},
                {"actor": "bot", "action": "test", "fail_count": 1, "success_count": 5, "heal_rate": 0.833},
            ],
        }

    def test_heal_distribution_forwarded_to_llm(self):
        lens = _make_lens()
        captured: dict = {}

        def mock_llm_thresholds(
            freq_distribution, cooccurrence_distribution, heal_distribution=None
        ):
            captured["heal_distribution"] = list(heal_distribution) if heal_distribution else []
            return {}

        with patch(
            "tools.oracle.lenses.lens_workflow_patterns._llm_anomaly_thresholds",
            side_effect=mock_llm_thresholds,
        ):
            lens._calibrate_anomaly_thresholds(self._analysis_with_heal_candidates())

        # Should have forwarded [3.0, 1.0] (fail_counts from candidates)
        assert sorted(captured["heal_distribution"]) == [1.0, 3.0]

    def test_empty_sessions_returns_empty(self):
        lens = _make_lens()
        result = lens._calibrate_anomaly_thresholds({"sessions": {}, "failed_then_done": []})
        assert result == {}


# ---------------------------------------------------------------------------
# _z_score_severity — optional calibrated boundaries
# ---------------------------------------------------------------------------

class TestZScoreSeverityCalibrated:
    """_z_score_severity accepts optional z_critical / z_warning kwargs."""

    POP = [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_default_boundaries(self):
        # value = 14 => z ~ (14-3)/stdev, well above 3 sigma
        result = _z_score_severity(14.0, self.POP)
        assert result == "critical"

    def test_calibrated_lower_critical(self):
        # With z_critical=1.5, a smaller deviation should still be critical
        result = _z_score_severity(5.5, self.POP, z_critical=1.5, z_warning=0.5)
        assert result in ("critical", "warning")

    def test_calibrated_lower_warning(self):
        result = _z_score_severity(4.0, self.POP, z_critical=10.0, z_warning=0.1)
        assert result == "warning"

    def test_info_when_below_warning(self):
        result = _z_score_severity(3.1, self.POP, z_critical=5.0, z_warning=4.0)
        assert result == "info"


# ---------------------------------------------------------------------------
# Scoring methods — calibrated Z values propagated
# ---------------------------------------------------------------------------

class TestScoringWithCalibratedZ:
    """score() must thread calibrated Z multipliers to all sub-methods."""

    def _minimal_analysis(self) -> dict:
        # Sessions with enough data to trigger ngram + co-occurrence scoring
        return {
            "sessions": {
                f"s{i}": ["code_generated", "test_executed", "test_passed", "security_scan"]
                for i in range(10)
            },
            "kanban_done": [
                {"task_type": "feature", "priority": "high", "created_at": "2026-01-01", "completed_at": "2026-01-02"}
            ] * 20,
            "failed_then_done": [
                {
                    "actor": "bot", "action": "deploy",
                    "fail_count": 5, "success_count": 3, "heal_rate": 0.375,
                },
                {
                    "actor": "bot", "action": "test",
                    "fail_count": 2, "success_count": 8, "heal_rate": 0.8,
                },
            ],
        }

    def test_score_runs_with_calibrated_dict(self):
        """score() must not crash when calibrated dict includes all new keys."""
        lens = _make_lens()
        calibrated = {
            "min_pattern_freq": 2,
            "cooccurrence_threshold": 0.5,
            "fallback_freq_denominator": 15.0,
            "fallback_session_denominator": 8.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 0.5,
            "adaptive_z_cooccurrence": 0.5,
            "adaptive_z_kanban": 0.5,
            "min_self_heal_fails": 1.0,
        }
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=calibrated):
            preds = lens.score(self._minimal_analysis())
        # Should produce predictions without error
        assert isinstance(preds, list)

    def test_calibrated_adaptive_z_ngram_respected(self):
        """Higher adaptive_z_ngram → stricter threshold → fewer ngram predictions."""
        lens = _make_lens()
        analysis = self._minimal_analysis()

        low_z = {"min_pattern_freq": 1, "adaptive_z_ngram": 0.0, "cooccurrence_threshold": 0.99}
        high_z = {"min_pattern_freq": 1, "adaptive_z_ngram": 100.0, "cooccurrence_threshold": 0.99}

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=low_z):
            preds_low = [p for p in lens.score(analysis) if p.category == "workflow_pattern"]

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=high_z):
            preds_high = [p for p in lens.score(analysis) if p.category == "workflow_pattern"]

        # Higher Z multiplier should produce fewer or equal ngram predictions
        assert len(preds_high) <= len(preds_low)

    def test_calibrated_min_self_heal_fails_respected(self):
        """Higher min_self_heal_fails → fewer self-healing predictions."""
        lens = _make_lens()
        analysis = self._minimal_analysis()

        low_floor = {"min_pattern_freq": 3, "min_self_heal_fails": 1.0, "adaptive_z_ngram": 1.0}
        high_floor = {"min_pattern_freq": 3, "min_self_heal_fails": 999.0, "adaptive_z_ngram": 1.0}

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=low_floor):
            preds_low = [p for p in lens.score(analysis) if p.category == "self_healing_candidate"]

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=high_floor):
            preds_high = [p for p in lens.score(analysis) if p.category == "self_healing_candidate"]

        assert len(preds_high) <= len(preds_low)

    def test_calibrated_adaptive_z_kanban_respected(self):
        """Higher adaptive_z_kanban → stricter threshold → fewer kanban predictions."""
        lens = _make_lens()
        analysis = self._minimal_analysis()

        low_z = {"min_pattern_freq": 1, "adaptive_z_kanban": 0.0}
        high_z = {"min_pattern_freq": 1, "adaptive_z_kanban": 100.0}

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=low_z):
            preds_low = [p for p in lens.score(analysis) if p.category == "recurring_task_type"]

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=high_z):
            preds_high = [p for p in lens.score(analysis) if p.category == "recurring_task_type"]

        assert len(preds_high) <= len(preds_low)

    def test_calibrated_adaptive_z_heal_respected(self):
        """Higher adaptive_z_heal → stricter fail-count floor → fewer self-healing predictions."""
        lens = _make_lens()
        analysis = self._minimal_analysis()

        low_z = {"min_self_heal_fails": 1.0, "adaptive_z_heal": 0.0}
        high_z = {"min_self_heal_fails": 1.0, "adaptive_z_heal": 100.0}

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=low_z):
            preds_low = [p for p in lens.score(analysis) if p.category == "self_healing_candidate"]

        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=high_z):
            preds_high = [p for p in lens.score(analysis) if p.category == "self_healing_candidate"]

        assert len(preds_high) <= len(preds_low)


class TestProposeUsesCalibration:
    """propose() must use calibrated heal_reflex_min_rate instead of the static default."""

    def _make_heal_prediction(self, heal_rate: float):
        from tools.oracle.prediction import OraclePrediction
        pred = OraclePrediction(
            lens="workflow_pattern",
            title="Self-Healing Candidate: bot / 'deploy'",
            description="bot failed 3x then succeeded",
            confidence=0.7,
            severity="warning",
            category="self_healing_candidate",
            data={
                "actor": "bot",
                "action": "deploy",
                "fail_count": 3,
                "success_count": 2,
                "heal_rate": heal_rate,
            },
        )
        return pred

    def test_propose_uses_calibrated_heal_reflex_min_rate_above(self):
        """When heal_rate >= calibrated heal_reflex_min_rate, recommend the reflex."""
        lens = _make_lens()
        lens._calibrated = {"heal_reflex_min_rate": 0.5}
        pred = self._make_heal_prediction(heal_rate=0.6)
        result = lens.propose([pred])
        assert any("Genesis Heal reflex" in r for r in result[0].recommendations)
        assert not any("manual review" in r for r in result[0].recommendations)

    def test_propose_uses_calibrated_heal_reflex_min_rate_below(self):
        """When heal_rate < calibrated heal_reflex_min_rate, recommend manual review."""
        lens = _make_lens()
        lens._calibrated = {"heal_reflex_min_rate": 0.9}
        pred = self._make_heal_prediction(heal_rate=0.6)
        result = lens.propose([pred])
        assert any("manual review" in r for r in result[0].recommendations)
        assert not any("Genesis Heal reflex" in r and "≥" in r for r in result[0].recommendations)

    def test_propose_no_hardcoded_07_string(self):
        """The literal '0.7' must not appear in any recommendation text — use the variable."""
        lens = _make_lens()
        lens._calibrated = {}  # falls back to _HEAL_REFLEX_MIN_RATE (0.7 by default)
        pred = self._make_heal_prediction(heal_rate=0.3)
        result = lens.propose([pred])
        for rec in result[0].recommendations:
            assert "0.7" not in rec, f"Hardcoded '0.7' found in recommendation: {rec!r}"

    def test_propose_fallback_to_module_default_without_calibrated(self):
        """propose() works correctly when _calibrated has not been set."""
        lens = _make_lens()
        pred = self._make_heal_prediction(heal_rate=_HEAL_REFLEX_MIN_RATE + 0.1)
        result = lens.propose([pred])
        assert any("Genesis Heal reflex" in r for r in result[0].recommendations)


class TestAdaptiveZHealConstant:
    """_ADAPTIVE_Z_HEAL is exported and has the expected default."""

    def test_adaptive_z_heal_default(self):
        assert _ADAPTIVE_Z_HEAL == pytest.approx(0.0)

    def test_llm_returns_adaptive_z_heal(self):
        payload = {
            "min_pattern_freq": 3,
            "cooccurrence_threshold": 0.8,
            "fallback_freq_denominator": 20.0,
            "fallback_session_denominator": 10.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 1.0,
            "adaptive_z_cooccurrence": 1.0,
            "adaptive_z_kanban": 1.0,
            "min_self_heal_fails": 2.0,
            "adaptive_z_heal": 0.75,
            "heal_reflex_min_rate": 0.65,
        }
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([5.0, 10.0], [0.7], heal_distribution=[2.0, 3.0])
        assert result.get("adaptive_z_heal") == pytest.approx(0.75)
        assert result.get("heal_reflex_min_rate") == pytest.approx(0.65)

    def test_llm_adaptive_z_heal_clamped_non_negative(self):
        payload = {
            "min_pattern_freq": 3,
            "cooccurrence_threshold": 0.8,
            "fallback_freq_denominator": 20.0,
            "fallback_session_denominator": 10.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 1.0,
            "adaptive_z_cooccurrence": 1.0,
            "adaptive_z_kanban": 1.0,
            "min_self_heal_fails": 2.0,
            "adaptive_z_heal": -5.0,
            "heal_reflex_min_rate": 0.7,
        }
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([5.0], [0.8], heal_distribution=[2.0, 3.0])
        assert result.get("adaptive_z_heal", 0.0) >= 0.0

    def test_llm_heal_reflex_min_rate_clamped_to_0_1(self):
        payload = {
            "min_pattern_freq": 3,
            "cooccurrence_threshold": 0.8,
            "fallback_freq_denominator": 20.0,
            "fallback_session_denominator": 10.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 1.0,
            "adaptive_z_cooccurrence": 1.0,
            "adaptive_z_kanban": 1.0,
            "min_self_heal_fails": 2.0,
            "adaptive_z_heal": 0.5,
            "heal_reflex_min_rate": 2.5,  # above 1.0
        }
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([5.0], [0.8], heal_distribution=[2.0, 3.0])
        assert result.get("heal_reflex_min_rate", 0.7) <= 1.0

    def test_heal_keys_not_returned_without_heal_distribution(self):
        """adaptive_z_heal and heal_reflex_min_rate are only requested when heal data is present."""
        payload = {
            "min_pattern_freq": 3,
            "cooccurrence_threshold": 0.8,
            "fallback_freq_denominator": 20.0,
            "fallback_session_denominator": 10.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 1.0,
            "adaptive_z_cooccurrence": 1.0,
            "adaptive_z_kanban": 1.0,
            # heal keys absent — LLM wouldn't include them without heal distribution
        }
        router = _mock_llm_response(payload)
        with patch(_LLM_ROUTER_PATH, return_value=router):
            result = _llm_anomaly_thresholds([5.0, 10.0], [0.8, 0.9])
        # Keys absent from payload → not in result
        assert "adaptive_z_heal" not in result
        assert "heal_reflex_min_rate" not in result
