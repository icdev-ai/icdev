# CUI // SP-CTI
"""Tests for Oracle WorkflowPatternLens anomaly detection.

Covers:
  - _percentile_rank, _score_pattern, _adaptive_threshold, _z_score_severity helpers
  - _score_ngram_patterns, _score_cooccurrence_pairs, _score_kanban_task_patterns,
    _score_self_healing scoring methods
  - _llm_anomaly_thresholds with mocked LLM (happy path + error fallback)
  - _calibrate_anomaly_thresholds integration
  - propose() recommendations
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from tools.oracle.lenses.lens_workflow_patterns import (
    _percentile_rank,
    _score_pattern,
    _adaptive_threshold,
    _z_score_severity,
    _llm_anomaly_thresholds,
    WorkflowPatternLens,
)


# ---------------------------------------------------------------------------
# Helper: _percentile_rank
# ---------------------------------------------------------------------------

class TestPercentileRank:
    def test_empty_population_returns_one(self):
        assert _percentile_rank(5.0, []) == 1.0

    def test_single_value_returns_one(self):
        assert _percentile_rank(3.0, [3.0]) == 1.0

    def test_value_above_all_returns_one(self):
        assert _percentile_rank(10.0, [1.0, 2.0, 3.0]) == 1.0

    def test_value_below_all_returns_zero(self):
        result = _percentile_rank(0.5, [1.0, 2.0, 3.0])
        assert result == 0.0

    def test_value_at_median(self):
        result = _percentile_rank(2.0, [1.0, 2.0, 3.0])
        assert result == pytest.approx(2 / 3, rel=1e-6)

    def test_fraction_in_range(self):
        result = _percentile_rank(3.0, [1.0, 2.0, 3.0, 4.0, 5.0])
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# Helper: _score_pattern
# ---------------------------------------------------------------------------

class TestScorePattern:
    def test_no_population_uses_fallback_denominator(self):
        # freq=10 / fallback_freq_denominator=20 = 0.5 (freq_weight=0.6)
        # session=5 / fallback_session_denominator=10 = 0.5 (consistency_weight=0.4)
        # expected = 0.6*0.5 + 0.4*0.5 = 0.5
        score = _score_pattern(10, 5)
        assert score == pytest.approx(0.5, abs=0.01)

    def test_full_population_at_max(self):
        # Value at top of population → freq_score and consistency approach 1.0
        population = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        score = _score_pattern(8, 8, population, population)
        assert score > 0.9

    def test_sparse_population_uses_max_normalization(self):
        # 3 samples < min_population_percentile=5 → normalize by observed max
        score = _score_pattern(3, 2, [1.0, 2.0, 3.0], [1.0, 2.0])
        assert 0.0 <= score <= 1.0

    def test_score_in_zero_one_range(self):
        for freq in [1, 5, 20]:
            for sessions in [1, 3, 10]:
                s = _score_pattern(freq, sessions)
                assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# Helper: _adaptive_threshold
# ---------------------------------------------------------------------------

class TestAdaptiveThreshold:
    def test_too_few_values_returns_fallback(self):
        result = _adaptive_threshold([1.0, 2.0], fallback=5.0, z=1.0)
        assert result == 5.0

    def test_enough_values_returns_mean_plus_z_sigma(self):
        # population=[0, 2, 4, 6, 8], mean=4, pstdev=sqrt(8)≈2.83, z=1
        values = [0.0, 2.0, 4.0, 6.0, 8.0]
        import statistics
        expected = statistics.mean(values) + 1.0 * statistics.pstdev(values)
        result = _adaptive_threshold(values, fallback=0.0, z=1.0)
        assert result == pytest.approx(expected, rel=1e-6)

    def test_z_zero_returns_mean(self):
        values = [2.0, 4.0, 6.0, 8.0, 10.0]
        import statistics
        result = _adaptive_threshold(values, fallback=0.0, z=0.0)
        assert result == pytest.approx(statistics.mean(values), rel=1e-6)

    def test_boundary_exactly_min_population(self):
        # Exactly min_population_adaptive=5 values → uses adaptive path
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _adaptive_threshold(values, fallback=99.0, z=0.0)
        assert result != 99.0  # not fallback


# ---------------------------------------------------------------------------
# Helper: _z_score_severity
# ---------------------------------------------------------------------------

class TestZScoreSeverity:
    def test_too_few_returns_info(self):
        assert _z_score_severity(100.0, [1.0, 2.0]) == "info"

    def test_below_mean_returns_info(self):
        pop = [10.0, 10.0, 10.0, 10.0, 10.0]
        assert _z_score_severity(9.0, pop) == "info"

    def test_at_critical_boundary(self):
        # pop=[0,1,2,3,4]: mean=2, pstdev=sqrt(2)≈1.414; Z=3 → value≈6.24; use 7.0
        pop = [0.0, 1.0, 2.0, 3.0, 4.0]
        assert _z_score_severity(7.0, pop) == "critical"

    def test_at_warning_boundary(self):
        # pop=[0,1,2,3,4]: Z=2 → value≈4.83; use 5.0 (Z≈2.12 > 2.0)
        pop = [0.0, 1.0, 2.0, 3.0, 4.0]
        assert _z_score_severity(5.0, pop) == "warning"

    def test_calibrated_overrides(self):
        # With custom z_critical=1.5, Z≈2.12 for value=5 → critical
        pop = [0.0, 1.0, 2.0, 3.0, 4.0]
        result = _z_score_severity(5.0, pop, z_critical=1.5, z_warning=0.5)
        assert result == "critical"

    def test_zero_sigma_population_above_mean(self):
        # All same values → sigma=0, value > mean → warning
        pop = [5.0, 5.0, 5.0, 5.0, 5.0]
        assert _z_score_severity(6.0, pop) == "warning"

    def test_zero_sigma_population_at_mean(self):
        pop = [5.0, 5.0, 5.0, 5.0, 5.0]
        assert _z_score_severity(5.0, pop) == "info"


# ---------------------------------------------------------------------------
# LLM Anomaly Threshold Calibration
# ---------------------------------------------------------------------------

class TestLLMAnomalyThresholds:
    def test_empty_distributions_returns_empty(self):
        result = _llm_anomaly_thresholds([], [], None)
        assert result == {}

    def test_llm_unavailable_returns_empty(self):
        # LLM imports are local inside _llm_anomaly_thresholds; make the module unavailable
        import sys
        with patch.dict(sys.modules, {"tools.llm.router": None}):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0], [0.5, 0.6], None)
        assert result == {}

    def _mock_llm_call(self, payload: dict):
        """Return (mock_router, mock_response) for patching inside _llm_anomaly_thresholds."""
        mock_response = MagicMock()
        mock_response.content = json.dumps(payload)
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_response
        return mock_router

    def test_llm_happy_path_returns_clamped_values(self):
        payload = {
            "min_pattern_freq": 4,
            "cooccurrence_threshold": 0.75,
            "fallback_freq_denominator": 25.0,
            "fallback_session_denominator": 12.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 1.2,
            "adaptive_z_cooccurrence": 0.8,
            "adaptive_z_kanban": 1.0,
        }
        mock_router = self._mock_llm_call(payload)
        mock_router_cls = MagicMock(return_value=mock_router)

        with patch("tools.llm.router.LLMRouter", mock_router_cls), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0, 4.0], [0.5, 0.6, 0.7], None)

        assert result.get("min_pattern_freq") == pytest.approx(4.0)
        assert result.get("cooccurrence_threshold") == pytest.approx(0.75)
        assert result.get("z_critical") == pytest.approx(3.0)

    def test_llm_clamps_cooccurrence_to_one(self):
        payload = {
            "min_pattern_freq": 2,
            "cooccurrence_threshold": 1.5,  # exceeds 1.0 → clamped to 1.0
            "fallback_freq_denominator": 10.0,
            "fallback_session_denominator": 5.0,
            "z_critical": 3.0,
            "z_warning": 2.0,
            "adaptive_z_ngram": 1.0,
            "adaptive_z_cooccurrence": 1.0,
            "adaptive_z_kanban": 1.0,
        }
        mock_router = self._mock_llm_call(payload)
        mock_router_cls = MagicMock(return_value=mock_router)

        with patch("tools.llm.router.LLMRouter", mock_router_cls), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0, 4.0], [0.5, 0.6, 0.7], None)

        assert result.get("cooccurrence_threshold") == pytest.approx(1.0)

    def test_llm_returns_markdown_fenced_json(self):
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
        }
        mock_response = MagicMock()
        mock_response.content = "```json\n" + json.dumps(payload) + "\n```"
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_response
        mock_router_cls = MagicMock(return_value=mock_router)

        with patch("tools.llm.router.LLMRouter", mock_router_cls), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0, 4.0], [0.5, 0.6, 0.7], None)

        assert "min_pattern_freq" in result

    def test_with_heal_distribution_includes_heal_keys(self):
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
            "heal_reflex_min_rate": 0.7,
        }
        mock_router = self._mock_llm_call(payload)
        mock_router_cls = MagicMock(return_value=mock_router)

        with patch("tools.llm.router.LLMRouter", mock_router_cls), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0, 4.0], [0.5, 0.6], [2.0, 3.0, 4.0])

        assert "min_self_heal_fails" in result
        assert "adaptive_z_heal" in result
        assert "heal_reflex_min_rate" in result


# ---------------------------------------------------------------------------
# WorkflowPatternLens — scoring methods with mocked analysis data
# ---------------------------------------------------------------------------

def _make_lens() -> WorkflowPatternLens:
    return WorkflowPatternLens()


class TestScoreNgramPatterns:
    """_score_ngram_patterns with controlled analysis data."""

    def _analysis(self, sessions=None):
        if sessions is None:
            sessions = {
                "s1": ["code_generated", "test_executed", "test_passed"] * 5,
                "s2": ["code_generated", "test_executed", "test_passed"] * 5,
                "s3": ["code_generated", "test_executed", "test_passed"] * 5,
                "s4": ["code_generated", "test_executed", "test_passed"] * 5,
            }
        return {"sessions": sessions, "kanban_done": [], "failed_then_done": []}

    def test_empty_sessions_returns_empty(self):
        lens = _make_lens()
        result = lens._score_ngram_patterns({"sessions": {}, "kanban_done": [], "failed_then_done": []})
        assert result == []

    def test_frequent_pattern_surfaces(self):
        lens = _make_lens()
        result = lens._score_ngram_patterns(self._analysis())
        assert len(result) > 0
        categories = {p.category for p in result}
        assert "workflow_pattern" in categories

    def test_prediction_has_required_fields(self):
        lens = _make_lens()
        result = lens._score_ngram_patterns(self._analysis())
        for pred in result:
            assert 0.0 <= pred.confidence <= 1.0
            assert "pattern" in pred.data
            assert "frequency" in pred.data
            assert "session_spread" in pred.data
            assert "automation_potential" in pred.data

    def test_automation_potential_high_for_automated_events(self):
        lens = _make_lens()
        # All events are in the "automated" set
        sessions = {
            f"s{i}": ["code_generated", "test_executed", "test_passed"] * 4
            for i in range(5)
        }
        result = lens._score_ngram_patterns({"sessions": sessions, "kanban_done": [], "failed_then_done": []})
        potentials = {p.data["automation_potential"] for p in result}
        # At least some should be "high"
        assert "high" in potentials

    def test_calibrated_overrides_applied(self):
        lens = _make_lens()
        # min_pattern_freq=100 → no patterns should surface since freq is low
        result = lens._score_ngram_patterns(
            self._analysis(), calibrated={"min_pattern_freq": 100, "adaptive_z_ngram": 0.0}
        )
        assert result == []


class TestScoreCooccurrencePairs:
    def _analysis(self, sessions=None):
        if sessions is None:
            sessions = {
                f"s{i}": ["code_generated", "test_executed", "security_scan"]
                for i in range(10)
            }
        return {"sessions": sessions, "kanban_done": [], "failed_then_done": []}

    def test_empty_returns_empty(self):
        lens = _make_lens()
        result = lens._score_cooccurrence_pairs({"sessions": {}, "kanban_done": [], "failed_then_done": []})
        assert result == []

    def test_high_cooccurrence_pair_surfaces(self):
        lens = _make_lens()
        # Threshold lowered to 0.0 to ensure pairs surface
        result = lens._score_cooccurrence_pairs(
            self._analysis(), calibrated={"cooccurrence_threshold": 0.0, "adaptive_z_cooccurrence": 0.0}
        )
        assert len(result) > 0

    def test_prediction_fields(self):
        lens = _make_lens()
        result = lens._score_cooccurrence_pairs(
            self._analysis(), calibrated={"cooccurrence_threshold": 0.0, "adaptive_z_cooccurrence": 0.0}
        )
        for pred in result:
            assert pred.category == "tool_composition_candidate"
            assert 0.0 <= pred.confidence <= 1.0
            assert "cooccurrence_rate" in pred.data
            assert 0.0 <= pred.data["cooccurrence_rate"] <= 1.0

    def test_high_threshold_filters_all(self):
        lens = _make_lens()
        # A appears in s1-s8+s9, B in s1-s8+s10 → co-occurrence rate = 8/10 = 0.8
        # threshold=0.99 with z=0 → adaptive = fallback = 0.99 > 0.8 → filtered
        sessions = {f"s{i}": ["A", "B"] for i in range(1, 9)}
        sessions["s9"] = ["A"]
        sessions["s10"] = ["B"]
        analysis = {"sessions": sessions, "kanban_done": [], "failed_then_done": []}
        result = lens._score_cooccurrence_pairs(
            analysis, calibrated={"cooccurrence_threshold": 0.99, "adaptive_z_cooccurrence": 0.0}
        )
        assert result == []


class TestScoreKanbanTaskPatterns:
    def _analysis(self, tasks=None):
        if tasks is None:
            tasks = [{"task_type": "build", "priority": "medium"} for _ in range(10)]
            tasks += [{"task_type": "bug", "priority": "high"} for _ in range(5)]
        return {"sessions": {}, "kanban_done": tasks, "failed_then_done": []}

    def test_empty_tasks_returns_empty(self):
        lens = _make_lens()
        result = lens._score_kanban_task_patterns({"sessions": {}, "kanban_done": [], "failed_then_done": []})
        assert result == []

    def test_too_few_tasks_returns_empty(self):
        lens = _make_lens()
        tasks = [{"task_type": "build"}] * 2  # fewer than min_pattern_freq=3
        result = lens._score_kanban_task_patterns({"sessions": {}, "kanban_done": tasks, "failed_then_done": []})
        assert result == []

    def test_dominant_type_surfaces(self):
        lens = _make_lens()
        result = lens._score_kanban_task_patterns(
            self._analysis(), calibrated={"min_pattern_freq": 3, "adaptive_z_kanban": 0.0}
        )
        assert len(result) > 0
        task_types = [p.data["task_type"] for p in result]
        assert "build" in task_types

    def test_prediction_fields(self):
        lens = _make_lens()
        result = lens._score_kanban_task_patterns(
            self._analysis(), calibrated={"min_pattern_freq": 3, "adaptive_z_kanban": 0.0}
        )
        for pred in result:
            assert pred.category == "recurring_task_type"
            assert "count" in pred.data
            assert "total_done" in pred.data
            assert "rate" in pred.data
            assert 0.0 <= pred.confidence <= 1.0


class TestScoreSelfHealing:
    def _candidates(self):
        return [
            {"actor": "builder", "action": "run_tests", "fail_count": 5, "success_count": 4, "heal_rate": 0.44},
            {"actor": "security", "action": "scan", "fail_count": 3, "success_count": 3, "heal_rate": 0.50},
            {"actor": "deploy", "action": "push", "fail_count": 8, "success_count": 6, "heal_rate": 0.43},
        ]

    def _analysis(self, candidates=None):
        return {
            "sessions": {},
            "kanban_done": [],
            "failed_then_done": candidates if candidates is not None else self._candidates(),
        }

    def test_empty_candidates_returns_empty(self):
        lens = _make_lens()
        result = lens._score_self_healing({"sessions": {}, "kanban_done": [], "failed_then_done": []})
        assert result == []

    def test_high_fail_count_surfaces(self):
        lens = _make_lens()
        result = lens._score_self_healing(
            self._analysis(), calibrated={"min_self_heal_fails": 2.0, "adaptive_z_heal": 0.0}
        )
        assert len(result) > 0

    def test_prediction_category(self):
        lens = _make_lens()
        result = lens._score_self_healing(
            self._analysis(), calibrated={"min_self_heal_fails": 2.0, "adaptive_z_heal": 0.0}
        )
        for pred in result:
            assert pred.category == "self_healing_candidate"

    def test_severity_assigned(self):
        lens = _make_lens()
        result = lens._score_self_healing(
            self._analysis(), calibrated={"min_self_heal_fails": 2.0, "adaptive_z_heal": 0.0}
        )
        valid_severities = {"info", "warning", "critical"}
        for pred in result:
            assert pred.severity in valid_severities

    def test_confidence_in_range(self):
        lens = _make_lens()
        result = lens._score_self_healing(
            self._analysis(), calibrated={"min_self_heal_fails": 2.0, "adaptive_z_heal": 0.0}
        )
        for pred in result:
            assert 0.0 <= pred.confidence <= 1.0

    def test_floor_filters_low_fail_counts(self):
        lens = _make_lens()
        # min_self_heal_fails=10 → all candidates with fail_count < 10 filtered out
        result = lens._score_self_healing(
            self._analysis(), calibrated={"min_self_heal_fails": 10.0, "adaptive_z_heal": 0.0}
        )
        assert result == []


# ---------------------------------------------------------------------------
# _calibrate_anomaly_thresholds (integration — mocks LLM)
# ---------------------------------------------------------------------------

class TestCalibrateAnomalyThresholds:
    def _analysis(self):
        sessions = {
            f"s{i}": ["code_generated", "test_executed", "test_passed"] * 3
            for i in range(6)
        }
        candidates = [
            {"actor": "builder", "action": "run", "fail_count": 3, "success_count": 2, "heal_rate": 0.4},
            {"actor": "sec", "action": "scan", "fail_count": 5, "success_count": 3, "heal_rate": 0.38},
        ]
        return {"sessions": sessions, "kanban_done": [], "failed_then_done": candidates}

    def test_returns_dict(self):
        lens = _make_lens()
        with patch("tools.oracle.lenses.lens_workflow_patterns._llm_anomaly_thresholds", return_value={}):
            result = lens._calibrate_anomaly_thresholds(self._analysis())
        assert isinstance(result, dict)

    def test_empty_analysis_returns_empty(self):
        lens = _make_lens()
        result = lens._calibrate_anomaly_thresholds({"sessions": {}, "kanban_done": [], "failed_then_done": []})
        assert result == {}

    def test_llm_overrides_propagated(self):
        overrides = {"min_pattern_freq": 5, "cooccurrence_threshold": 0.9}
        lens = _make_lens()
        with patch("tools.oracle.lenses.lens_workflow_patterns._llm_anomaly_thresholds", return_value=overrides):
            result = lens._calibrate_anomaly_thresholds(self._analysis())
        assert result.get("min_pattern_freq") == 5
        assert result.get("cooccurrence_threshold") == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# propose() — recommendation enrichment
# ---------------------------------------------------------------------------

class TestPropose:
    def _make_prediction(self, category, data=None, confidence=0.8):
        from tools.oracle.prediction import OraclePrediction
        return OraclePrediction(
            lens="workflow_pattern",
            title="Test",
            description="desc",
            confidence=confidence,
            severity="info",
            category=category,
            data=data or {},
        )

    def test_workflow_pattern_recommendations(self):
        lens = _make_lens()
        pred = self._make_prediction("workflow_pattern", {"pattern": ["a", "b", "c"], "automation_potential": "high"})
        result = lens.propose([pred])
        assert len(result[0].recommendations) > 0
        recs_text = " ".join(result[0].recommendations)
        assert "Genesis" in recs_text or "automation" in recs_text.lower()

    def test_tool_composition_recommendations(self):
        lens = _make_lens()
        pred = self._make_prediction("tool_composition_candidate", {
            "tool_a": "code_generated", "tool_b": "test_executed", "cooccurrence_rate": 0.9
        })
        result = lens.propose([pred])
        recs_text = " ".join(result[0].recommendations)
        assert "composite" in recs_text.lower() or "code_generated" in recs_text

    def test_recurring_task_type_recommendations(self):
        lens = _make_lens()
        pred = self._make_prediction("recurring_task_type", {"task_type": "build"})
        result = lens.propose([pred])
        recs = result[0].recommendations
        assert any("build" in r for r in recs)

    def test_self_healing_above_threshold_recommends_reflex(self):
        lens = _make_lens()
        lens._calibrated = {"heal_reflex_min_rate": 0.7}
        pred = self._make_prediction("self_healing_candidate", {
            "actor": "builder", "action": "run_tests", "heal_rate": 0.9, "fail_count": 5, "success_count": 8
        })
        result = lens.propose([pred])
        recs_text = " ".join(result[0].recommendations)
        assert "Heal" in recs_text or "reflex" in recs_text.lower()

    def test_self_healing_below_threshold_recommends_monitor(self):
        lens = _make_lens()
        lens._calibrated = {"heal_reflex_min_rate": 0.7}
        pred = self._make_prediction("self_healing_candidate", {
            "actor": "builder", "action": "run_tests", "heal_rate": 0.3, "fail_count": 5, "success_count": 2
        })
        result = lens.propose([pred])
        recs_text = " ".join(result[0].recommendations)
        assert "Monitor" in recs_text or "review" in recs_text.lower()

    def test_unknown_category_passes_through_unchanged(self):
        lens = _make_lens()
        pred = self._make_prediction("unknown_category", {})
        result = lens.propose([pred])
        assert result[0].recommendations == []
