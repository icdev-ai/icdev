# CUI // SP-CTI
"""Tests for WorkflowPatternLens anomaly detection — hardcoded_threshold replacement.

Covers:
  - _percentile_rank: boundary conditions and correct fraction
  - _score_pattern: fallback, sparse, and full-population paths
  - _adaptive_threshold: fallback when < min_population, mean+z*sigma otherwise
  - _z_score_severity: severity classification by Z-score
  - _automation_potential: heuristic classification
  - _llm_anomaly_thresholds: graceful empty-input return and JSON clamping
  - WorkflowPatternLens.score: end-to-end with synthetic analysis data
  - YAML config loading: args/lens_workflow_patterns.yaml round-trips correctly
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import helpers and lens directly (no DB required for unit tests)
from tools.oracle.lenses.lens_workflow_patterns import (
    WorkflowPatternLens,
    _adaptive_threshold,
    _automation_potential,
    _llm_anomaly_thresholds,
    _ngrams,
    _percentile_rank,
    _score_pattern,
    _z_score_severity,
    _CODE_DEFAULTS,
    _ARGS_PATH,
)


# ---------------------------------------------------------------------------
# _percentile_rank
# ---------------------------------------------------------------------------

class TestPercentileRank:
    def test_empty_population_returns_one(self):
        assert _percentile_rank(5.0, []) == 1.0

    def test_single_element_returns_one(self):
        assert _percentile_rank(3.0, [3.0]) == 1.0

    def test_value_at_min_returns_fraction(self):
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile_rank(1.0, pop) == pytest.approx(0.2)

    def test_value_at_max_returns_one(self):
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile_rank(5.0, pop) == pytest.approx(1.0)

    def test_value_above_max_returns_one(self):
        pop = [1.0, 2.0, 3.0]
        assert _percentile_rank(100.0, pop) == pytest.approx(1.0)

    def test_value_below_min_returns_zero(self):
        pop = [2.0, 3.0, 4.0]
        assert _percentile_rank(1.0, pop) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _score_pattern
# ---------------------------------------------------------------------------

class TestScorePattern:
    def test_fallback_path_no_population(self):
        # freq=10 / 20.0 = 0.5, session=5 / 10.0 = 0.5 → 0.6*0.5 + 0.4*0.5 = 0.5
        score = _score_pattern(10, 5, None, None)
        assert 0.0 <= score <= 1.0
        assert score == pytest.approx(0.5, abs=0.01)

    def test_fallback_caps_at_one(self):
        score = _score_pattern(100, 100, None, None)
        assert score <= 1.0

    def test_sparse_population_normalises_by_max(self):
        # freq_population has 3 elements (< min_population_percentile=5)
        freq_pop = [2.0, 5.0, 10.0]
        score = _score_pattern(10, 1, freq_pop, None)
        # freq_score = min(1.0, 10/10) = 1.0; consistency fallback = min(1.0, 1/10)=0.1
        assert score == pytest.approx(0.6 * 1.0 + 0.4 * 0.1, abs=0.01)

    def test_full_population_uses_percentile_rank(self):
        freq_pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        session_pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        score = _score_pattern(5, 5, freq_pop, session_pop)
        # Both at max → percentile_rank = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_result_bounded_zero_to_one(self):
        for freq in (0, 1, 5, 50):
            for sess in (0, 1, 10):
                s = _score_pattern(freq, sess)
                assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# _adaptive_threshold
# ---------------------------------------------------------------------------

class TestAdaptiveThreshold:
    def test_falls_back_when_insufficient_data(self):
        result = _adaptive_threshold([1.0, 2.0], fallback=5.0, z=1.0)
        assert result == 5.0  # < min_population_adaptive (5)

    def test_uses_mean_plus_sigma_with_sufficient_data(self):
        values = [2.0, 4.0, 6.0, 8.0, 10.0]  # mean=6, pstdev≈2.83
        result = _adaptive_threshold(values, fallback=3.0, z=1.0)
        import statistics
        mu = statistics.mean(values)
        sigma = statistics.pstdev(values)
        assert result == pytest.approx(mu + 1.0 * sigma, abs=0.001)

    def test_z_zero_returns_mean(self):
        values = [2.0, 4.0, 6.0, 8.0, 10.0]
        import statistics
        result = _adaptive_threshold(values, fallback=0.0, z=0.0)
        assert result == pytest.approx(statistics.mean(values), abs=0.001)

    def test_higher_z_gives_stricter_threshold(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        t0 = _adaptive_threshold(values, fallback=0.0, z=0.0)
        t1 = _adaptive_threshold(values, fallback=0.0, z=1.0)
        t2 = _adaptive_threshold(values, fallback=0.0, z=2.0)
        assert t0 <= t1 <= t2


# ---------------------------------------------------------------------------
# _z_score_severity
# ---------------------------------------------------------------------------

class TestZScoreSeverity:
    def test_info_when_below_min_population(self):
        assert _z_score_severity(100.0, [1.0, 2.0]) == "info"

    def test_info_when_below_warning_threshold(self):
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _z_score_severity(3.0, pop) == "info"

    def test_warning_between_thresholds(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        # Place value at mean + 2.5*sigma (between warning=2.0 and critical=3.0)
        value = mu + 2.5 * sigma
        assert _z_score_severity(value, pop) == "warning"

    def test_critical_above_z_critical(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        value = mu + 3.5 * sigma
        assert _z_score_severity(value, pop) == "critical"

    def test_calibrated_overrides_applied(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        # With very high z_critical=10.0, even extreme value should be "warning"
        value = mu + 5.0 * sigma
        assert _z_score_severity(value, pop, z_critical=10.0, z_warning=1.0) == "warning"

    def test_zero_sigma_population(self):
        pop = [5.0, 5.0, 5.0, 5.0, 5.0]
        assert _z_score_severity(6.0, pop) == "warning"
        assert _z_score_severity(5.0, pop) == "info"


# ---------------------------------------------------------------------------
# _automation_potential
# ---------------------------------------------------------------------------

class TestAutomationPotential:
    def test_manual_event_returns_low(self):
        pattern = ("code_generated", "approval_granted", "test_executed")
        assert _automation_potential(pattern) == "low"

    def test_all_automated_returns_high(self):
        pattern = ("code_generated", "test_executed", "test_passed")
        assert _automation_potential(pattern) == "high"

    def test_mixed_no_manual_below_ratio_returns_medium(self):
        # 1 automated out of 3 = 0.33 < 0.5 → medium
        pattern = ("code_generated", "unknown_event", "another_event")
        assert _automation_potential(pattern) == "medium"

    def test_empty_pattern(self):
        # No events → hits_auto/len(pattern) would div-by-zero without guard
        result = _automation_potential(())
        assert result in ("low", "medium", "high")


# ---------------------------------------------------------------------------
# _ngrams
# ---------------------------------------------------------------------------

class TestNgrams:
    def test_trigrams(self):
        seq = ["a", "b", "c", "d"]
        result = _ngrams(seq, 3)
        assert result == [("a", "b", "c"), ("b", "c", "d")]

    def test_returns_empty_when_seq_shorter_than_n(self):
        assert _ngrams(["a", "b"], 3) == []

    def test_single_ngram(self):
        assert _ngrams(["x", "y", "z"], 3) == [("x", "y", "z")]


# ---------------------------------------------------------------------------
# _llm_anomaly_thresholds
# ---------------------------------------------------------------------------

class TestLlmAnomalyThresholds:
    def test_empty_inputs_return_empty_dict(self):
        result = _llm_anomaly_thresholds([], [], None)
        assert result == {}

    def test_llm_failure_returns_empty_dict(self):
        # LLMRouter is a lazy import inside the function; patch at its source module
        with patch("tools.llm.router.LLMRouter", side_effect=RuntimeError("unavailable")):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0], [0.5, 0.8], None)
        assert result == {}

    def _mock_router_context(self, response_content: str):
        """Return a context manager that injects a mock LLMRouter with the given response."""
        mock_response = MagicMock()
        mock_response.content = response_content
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        return patch("tools.llm.router.LLMRouter", return_value=mock_router_instance)

    def test_valid_llm_response_parsed_and_clamped(self):
        valid_json = json.dumps({
            "min_pattern_freq": 4.0,
            "cooccurrence_threshold": 0.75,
            "fallback_freq_denominator": 25.0,
            "fallback_session_denominator": 15.0,
            "z_critical": 2.5,
            "z_warning": 1.5,
            "adaptive_z_ngram": 1.2,
            "adaptive_z_cooccurrence": 0.8,
            "adaptive_z_kanban": 0.9,
        })
        with self._mock_router_context(valid_json):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0, 4.0, 5.0], [0.5, 0.7, 0.9])

        assert result["min_pattern_freq"] == pytest.approx(4.0)
        assert result["cooccurrence_threshold"] == pytest.approx(0.75)
        assert result["z_critical"] == pytest.approx(2.5)

    def test_llm_response_values_clamped_to_range(self):
        # cooccurrence_threshold must be in [0, 1]; supply 2.0 → clamped to 1.0
        invalid_json = json.dumps({
            "min_pattern_freq": 0.0,          # lo=1.0 → clamped to 1.0
            "cooccurrence_threshold": 2.0,    # hi=1.0 → clamped to 1.0
            "fallback_freq_denominator": 5.0,
            "fallback_session_denominator": 5.0,
            "z_critical": 0.5,                # lo=1.0 → clamped to 1.0
            "z_warning": 0.1,                 # lo=0.5 → clamped to 0.5
            "adaptive_z_ngram": -1.0,         # lo=0.0 → clamped to 0.0
            "adaptive_z_cooccurrence": 1.0,
            "adaptive_z_kanban": 1.0,
        })
        with self._mock_router_context(invalid_json):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0], [0.5, 0.8])

        assert result["min_pattern_freq"] == pytest.approx(1.0)
        assert result["cooccurrence_threshold"] == pytest.approx(1.0)
        assert result["z_critical"] == pytest.approx(1.0)
        assert result["z_warning"] == pytest.approx(0.5)
        assert result["adaptive_z_ngram"] == pytest.approx(0.0)

    def test_markdown_fence_stripped(self):
        fenced = "```json\n{\"min_pattern_freq\": 3.0, \"cooccurrence_threshold\": 0.8, \"fallback_freq_denominator\": 20.0, \"fallback_session_denominator\": 10.0, \"z_critical\": 3.0, \"z_warning\": 2.0, \"adaptive_z_ngram\": 1.0, \"adaptive_z_cooccurrence\": 1.0, \"adaptive_z_kanban\": 1.0}\n```"
        with self._mock_router_context(fenced):
            result = _llm_anomaly_thresholds([1.0, 2.0, 3.0], [0.5])

        assert "min_pattern_freq" in result


# ---------------------------------------------------------------------------
# WorkflowPatternLens.score — end-to-end with synthetic data
# ---------------------------------------------------------------------------

class TestWorkflowPatternLensScore:
    """End-to-end scoring without a DB — inject synthetic analysis output."""

    def _make_analysis(self):
        # 3 sessions with repeated 3-gram to trigger ngram detection
        sessions = {
            "s1": ["code_generated", "test_executed", "test_passed", "deployment_initiated"],
            "s2": ["code_generated", "test_executed", "test_passed", "security_scan"],
            "s3": ["code_generated", "test_executed", "test_passed", "compliance_check"],
            "s4": ["code_generated", "test_executed", "test_passed", "agent_task_completed"],
        }
        kanban_done = [
            {"task_type": "build", "id": str(i), "title": f"task {i}",
             "priority": "medium", "created_at": "2026-01-01", "completed_at": "2026-01-02"}
            for i in range(6)
        ] + [
            {"task_type": "bug", "id": str(i + 10), "title": f"bug {i}",
             "priority": "high", "created_at": "2026-01-01", "completed_at": "2026-01-02"}
            for i in range(3)
        ]
        failed_then_done = [
            {"actor": "builder-agent", "action": "generate_code",
             "fail_count": 3, "success_count": 7, "heal_rate": 0.7},
            {"actor": "builder-agent", "action": "run_tests",
             "fail_count": 2, "success_count": 5, "heal_rate": 0.714},
        ]
        return {
            "sessions": sessions,
            "kanban_done": kanban_done,
            "failed_then_done": failed_then_done,
        }

    def test_score_returns_predictions(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value={}):
            preds = lens.score(analysis)
        assert isinstance(preds, list)
        assert len(preds) > 0

    def test_predictions_sorted_by_confidence_descending(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value={}):
            preds = lens.score(analysis)
        confs = [p.confidence for p in preds]
        assert confs == sorted(confs, reverse=True)

    def test_ngram_predictions_have_correct_category(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value={}):
            preds = lens.score(analysis)
        ngram_preds = [p for p in preds if p.category == "workflow_pattern"]
        assert len(ngram_preds) > 0
        for p in ngram_preds:
            assert "pattern" in p.data
            assert "frequency" in p.data
            assert "automation_potential" in p.data

    def test_kanban_predictions_have_correct_category(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value={}):
            preds = lens.score(analysis)
        kanban_preds = [p for p in preds if p.category == "recurring_task_type"]
        # "build" appears 6 times → should surface
        task_types = {p.data["task_type"] for p in kanban_preds}
        assert "build" in task_types

    def test_self_healing_predictions_present(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value={}):
            preds = lens.score(analysis)
        heal_preds = [p for p in preds if p.category == "self_healing_candidate"]
        assert len(heal_preds) > 0

    def test_calibrated_thresholds_applied_to_scoring(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        # Force a very high min_pattern_freq to suppress all ngram predictions
        high_calibrated = {"min_pattern_freq": 10000.0}
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value=high_calibrated):
            preds = lens.score(analysis)
        ngram_preds = [p for p in preds if p.category == "workflow_pattern"]
        assert len(ngram_preds) == 0

    def test_empty_analysis_returns_empty_list(self):
        lens = WorkflowPatternLens()
        analysis = {"sessions": {}, "kanban_done": [], "failed_then_done": []}
        preds = lens.score(analysis)
        assert preds == []

    def test_confidence_values_in_range(self):
        lens = WorkflowPatternLens()
        analysis = self._make_analysis()
        with patch.object(lens, "_calibrate_anomaly_thresholds", return_value={}):
            preds = lens.score(analysis)
        for p in preds:
            assert 0.0 <= p.confidence <= 1.0, f"Out-of-range confidence: {p.confidence}"


# ---------------------------------------------------------------------------
# WorkflowPatternLens.propose — recommendation text
# ---------------------------------------------------------------------------

class TestWorkflowPatternLensPropose:
    def _make_prediction(self, category, data):
        from tools.oracle.base_lens import OraclePrediction
        return OraclePrediction(
            lens="workflow_pattern",
            title="Test",
            description="desc",
            confidence=0.8,
            severity="info",
            category=category,
            data=data,
        )

    def test_workflow_pattern_recommendations(self):
        lens = WorkflowPatternLens()
        lens._calibrated = {}
        pred = self._make_prediction("workflow_pattern", {
            "pattern": ["a", "b", "c"],
            "automation_potential": "high",
        })
        result = lens.propose([pred])
        assert any("goals/manifest.md" in r for r in result[0].recommendations)
        assert any("Genesis reflexes" in r for r in result[0].recommendations)

    def test_self_healing_recommendation_uses_calibrated_rate(self):
        lens = WorkflowPatternLens()
        lens._calibrated = {"heal_reflex_min_rate": 0.6}
        pred = self._make_prediction("self_healing_candidate", {
            "actor": "builder-agent",
            "action": "run_tests",
            "heal_rate": 0.65,  # above 0.6 → recommend reflex
        })
        result = lens.propose([pred])
        recs = result[0].recommendations
        assert any("Genesis Heal reflex" in r for r in recs)

    def test_self_healing_recommendation_below_rate_warns(self):
        lens = WorkflowPatternLens()
        lens._calibrated = {"heal_reflex_min_rate": 0.8}
        pred = self._make_prediction("self_healing_candidate", {
            "actor": "builder-agent",
            "action": "run_tests",
            "heal_rate": 0.5,  # below 0.8 → manual review
        })
        result = lens.propose([pred])
        recs = result[0].recommendations
        assert any("manual review" in r for r in recs)


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------

class TestYamlConfig:
    def test_args_yaml_exists(self):
        assert _ARGS_PATH.exists(), f"Missing config: {_ARGS_PATH}"

    def test_args_yaml_valid(self):
        import yaml
        data = yaml.safe_load(_ARGS_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_args_yaml_contains_required_keys(self):
        import yaml
        data = yaml.safe_load(_ARGS_PATH.read_text(encoding="utf-8"))
        required = [
            "min_pattern_freq", "cooccurrence_threshold", "window_sizes",
            "lookback_days", "fallback_freq_denominator", "fallback_session_denominator",
            "min_self_heal_fails", "heal_reflex_min_rate", "z_critical", "z_warning",
        ]
        for key in required:
            assert key in data, f"Missing key in YAML: {key}"

    def test_args_yaml_values_match_code_defaults(self):
        import yaml
        data = yaml.safe_load(_ARGS_PATH.read_text(encoding="utf-8"))
        # Verify YAML defaults align with code defaults for critical thresholds
        assert data["min_pattern_freq"] == _CODE_DEFAULTS["min_pattern_freq"]
        assert data["cooccurrence_threshold"] == pytest.approx(_CODE_DEFAULTS["cooccurrence_threshold"])
        assert data["z_critical"] == pytest.approx(_CODE_DEFAULTS["z_critical"])
        assert data["z_warning"] == pytest.approx(_CODE_DEFAULTS["z_warning"])
