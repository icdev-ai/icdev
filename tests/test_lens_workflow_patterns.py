# CUI // SP-CTI
"""Tests for WorkflowPatternLens helpers — anomaly-detection threshold logic."""

import pytest
from tools.oracle.lenses.lens_workflow_patterns import (
    _adaptive_threshold,
    _percentile_rank,
    _score_pattern,
    _z_score_severity,
    _FALLBACK_FREQ_DENOMINATOR,
    _FALLBACK_SESSION_DENOMINATOR,
    _PATTERN_CONF_FREQ_WEIGHT,
    _PATTERN_CONF_CONSISTENCY_WEIGHT,
    _Z_CRITICAL,
    _Z_WARNING,
    _ADAPTIVE_Z_NGRAM,
    _ADAPTIVE_Z_COOCCURRENCE,
    _ADAPTIVE_Z_KANBAN,
    _ADAPTIVE_Z_HEAL,
    _HEAL_REFLEX_MIN_RATE,
    _MAX_KANBAN_DONE,
    _AUTOMATION_HIGH_RATIO,
    _AUTOMATED_EVENT_TYPES,
    _MANUAL_EVENT_TYPES,
    _MIN_PERCENTILE_RANK_POPULATION,
    _automation_potential,
    WorkflowPatternLens,
)


class TestPercentileRank:
    def test_empty_population_returns_one(self):
        assert _percentile_rank(5.0, []) == 1.0

    def test_single_element_population_returns_one(self):
        assert _percentile_rank(3.0, [3.0]) == 1.0

    def test_value_below_all(self):
        assert _percentile_rank(0.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == 0.0

    def test_value_above_all(self):
        assert _percentile_rank(10.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == 1.0

    def test_value_at_median(self):
        result = _percentile_rank(3.0, [1.0, 2.0, 3.0, 4.0, 5.0])
        assert result == pytest.approx(0.6)

    def test_returns_fraction_in_0_1(self):
        pop = [float(i) for i in range(100)]
        result = _percentile_rank(50.0, pop)
        assert 0.0 <= result <= 1.0

    def test_higher_value_higher_rank(self):
        pop = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        assert _percentile_rank(9.0, pop) > _percentile_rank(5.0, pop)


class TestScorePatternWithPopulation:
    """_score_pattern uses percentile rank when ≥5 population samples exist."""

    def _make_pop(self):
        return [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    def test_sparse_data_uses_observed_max(self):
        # 3 samples (< 5): normalize by observed max, not hardcoded denominator
        pop = [2.0, 4.0, 8.0]
        result = _score_pattern(4, 2, pop, pop)
        # freq_score = min(1.0, 4 / 8.0) = 0.5; consistency = min(1.0, 2 / 8.0) = 0.25
        expected = round(_PATTERN_CONF_FREQ_WEIGHT * 0.5 + _PATTERN_CONF_CONSISTENCY_WEIGHT * 0.25, 3)
        assert result == pytest.approx(expected)

    def test_sparse_data_caps_at_one_when_exceeds_max(self):
        # value exceeds population max → capped at 1.0
        pop = [1.0, 2.0, 3.0]
        result = _score_pattern(10, 5, pop, pop)
        assert result == pytest.approx(1.0)

    def test_population_drives_freq_score(self):
        pop = self._make_pop()
        # freq=10 is max of [1..10] → percentile rank ~1.0
        result_high = _score_pattern(10, 1, pop, [1.0])
        # freq=1 is min of [1..10] → percentile rank ~0.1
        result_low = _score_pattern(1, 1, pop, [1.0])
        assert result_high > result_low

    def test_population_drives_session_score(self):
        pop = self._make_pop()
        # session_count=10 (max) vs session_count=1 (min)
        result_high = _score_pattern(5, 10, [5.0], pop)
        result_low = _score_pattern(5, 1, [5.0], pop)
        assert result_high > result_low

    def test_result_in_0_1(self):
        pop = [float(i) for i in range(1, 21)]
        result = _score_pattern(15, 8, pop, pop)
        assert 0.0 <= result <= 1.0

    def test_none_populations_use_linear_fallback(self):
        # No population at all → fixed-denominator fallback (configurable via args/lens_workflow_patterns.yaml)
        result = _score_pattern(10, 5)
        expected = round(
            _PATTERN_CONF_FREQ_WEIGHT * min(1.0, 10 / _FALLBACK_FREQ_DENOMINATOR)
            + _PATTERN_CONF_CONSISTENCY_WEIGHT * min(1.0, 5 / _FALLBACK_SESSION_DENOMINATOR),
            3,
        )
        assert result == expected

    def test_sparse_higher_than_no_population_when_max_is_small(self):
        # With a sparse population whose max < 20, data-driven score > fixed fallback
        pop = [1.0, 2.0, 4.0]  # max = 4 → freq_score = min(1.0, 3/4) = 0.75
        result = _score_pattern(3, 2, pop, pop)
        # vs no-population: 3/20 = 0.15, 2/10 = 0.2
        no_pop = _score_pattern(3, 2)
        assert result > no_pop


class TestAdaptiveThreshold:
    def test_fallback_when_too_few_values(self):
        assert _adaptive_threshold([1.0, 2.0, 3.0], fallback=5.0) == 5.0

    def test_returns_mean_plus_sigma_for_sufficient_data(self):
        import statistics
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(values)
        sigma = statistics.pstdev(values)
        assert _adaptive_threshold(values, fallback=0.0, z=1.0) == pytest.approx(mu + sigma)

    def test_z_zero_returns_mean(self):
        import statistics
        values = [2.0, 4.0, 6.0, 8.0, 10.0]
        assert _adaptive_threshold(values, fallback=0.0, z=0.0) == pytest.approx(statistics.mean(values))


class TestZScoreSeverity:
    def test_too_few_returns_info(self):
        assert _z_score_severity(100.0, [1.0, 2.0]) == "info"

    def test_z_above_3_critical(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        extreme = mu + 3.5 * sigma
        assert _z_score_severity(extreme, pop) == "critical"

    def test_z_between_2_and_3_warning(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        mid = mu + 2.5 * sigma
        assert _z_score_severity(mid, pop) == "warning"

    def test_z_below_2_info(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        assert _z_score_severity(mu, pop) == "info"


class TestConfigurableConstants:
    """All formerly-hardcoded thresholds are now read from args/lens_workflow_patterns.yaml."""

    def test_z_critical_default(self):
        assert _Z_CRITICAL == pytest.approx(3.0)

    def test_z_warning_default(self):
        assert _Z_WARNING == pytest.approx(2.0)

    def test_pattern_weights_sum_to_one(self):
        assert _PATTERN_CONF_FREQ_WEIGHT + _PATTERN_CONF_CONSISTENCY_WEIGHT == pytest.approx(1.0)

    def test_adaptive_z_ngram_default(self):
        assert _ADAPTIVE_Z_NGRAM == pytest.approx(1.0)

    def test_adaptive_z_cooccurrence_default(self):
        assert _ADAPTIVE_Z_COOCCURRENCE == pytest.approx(1.0)

    def test_adaptive_z_kanban_default(self):
        assert _ADAPTIVE_Z_KANBAN == pytest.approx(1.0)

    def test_adaptive_z_heal_default(self):
        assert _ADAPTIVE_Z_HEAL == pytest.approx(0.0)

    def test_heal_reflex_min_rate_default(self):
        assert _HEAL_REFLEX_MIN_RATE == pytest.approx(0.7)

    def test_score_pattern_uses_configured_weights(self):
        # With equal freq and session scores the result equals the weight sum times that score
        pop = [float(i) for i in range(1, 21)]
        pr = _percentile_rank(10.0, pop)
        result = _score_pattern(10, 10, pop, pop)
        expected = round(_PATTERN_CONF_FREQ_WEIGHT * pr + _PATTERN_CONF_CONSISTENCY_WEIGHT * pr, 3)
        assert result == pytest.approx(expected)

    def test_z_severity_uses_configured_critical_threshold(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        value = mu + (_Z_CRITICAL + 0.5) * sigma
        assert _z_score_severity(value, pop) == "critical"

    def test_z_severity_uses_configured_warning_threshold(self):
        import statistics
        pop = [1.0, 2.0, 3.0, 4.0, 5.0]
        mu = statistics.mean(pop)
        sigma = statistics.pstdev(pop)
        value = mu + (_Z_WARNING + 0.5) * sigma
        assert _z_score_severity(value, pop) in ("warning", "critical")


class TestCalibratedThresholdsThreaded:
    """LLM-calibrated thresholds are passed through to _score_kanban_task_patterns
    and _score_self_healing so no scoring path uses hardcoded magic numbers."""

    def _make_lens(self):
        from tools.oracle.lenses.lens_workflow_patterns import WorkflowPatternLens
        return WorkflowPatternLens()

    def test_kanban_respects_calibrated_min_pattern_freq(self):
        """A calibrated min_pattern_freq=10 should suppress tasks with count < 10."""
        lens = self._make_lens()
        # 5 tasks of type "feature" — below calibrated floor of 10
        analysis = {
            "kanban_done": [{"task_type": "feature"}] * 5,
            "sessions": {},
            "failed_then_done": [],
        }
        preds = lens._score_kanban_task_patterns(analysis, calibrated={"min_pattern_freq": 10})
        assert preds == []

    def test_kanban_uses_calibrated_min_freq_when_lower(self):
        """A calibrated min_pattern_freq=2 should surface tasks with count ≥ 2."""
        lens = self._make_lens()
        analysis = {
            "kanban_done": [{"task_type": "bug"}] * 3,
            "sessions": {},
            "failed_then_done": [],
        }
        preds = lens._score_kanban_task_patterns(analysis, calibrated={"min_pattern_freq": 2})
        assert any(p.data.get("task_type") == "bug" for p in preds)

    def test_self_healing_respects_calibrated_min_self_heal_fails(self):
        """A calibrated min_self_heal_fails=10 should suppress candidates with fail_count < 10."""
        lens = self._make_lens()
        analysis = {
            "kanban_done": [],
            "sessions": {},
            "failed_then_done": [
                {"actor": "builder", "action": "compile", "fail_count": 3,
                 "success_count": 2, "heal_rate": 0.4},
            ],
        }
        preds = lens._score_self_healing(analysis, calibrated={"min_self_heal_fails": 10})
        assert preds == []

    def test_self_healing_uses_calibrated_min_when_lower(self):
        """A calibrated min_self_heal_fails=1 should surface any candidate with fail_count ≥ 1."""
        lens = self._make_lens()
        analysis = {
            "kanban_done": [],
            "sessions": {},
            "failed_then_done": [
                {"actor": "tester", "action": "run_tests", "fail_count": 2,
                 "success_count": 5, "heal_rate": 0.71},
            ],
        }
        preds = lens._score_self_healing(analysis, calibrated={"min_self_heal_fails": 1})
        assert len(preds) == 1
        assert preds[0].data["actor"] == "tester"

    def test_no_calibrated_falls_back_to_module_defaults(self):
        """Passing calibrated=None falls back to module-level constants without error."""
        lens = self._make_lens()
        analysis = {
            "kanban_done": [{"task_type": "chore"}] * 5,
            "sessions": {},
            "failed_then_done": [],
        }
        # Should not raise even with calibrated=None
        preds = lens._score_kanban_task_patterns(analysis, calibrated=None)
        assert isinstance(preds, list)

    def test_calibrate_includes_heal_dist_when_candidates_present(self):
        """_calibrate_anomaly_thresholds passes heal distribution when candidates exist."""
        lens = self._make_lens()
        captured: dict = {}

        import tools.oracle.lenses.lens_workflow_patterns as _mod

        original = _mod._llm_anomaly_thresholds

        def _capture(freq_dist, cooc_dist, heal_dist=None):
            captured["heal_dist"] = heal_dist
            return {}

        _mod._llm_anomaly_thresholds = _capture
        try:
            analysis = {
                "sessions": {},
                "failed_then_done": [
                    {"actor": "a", "action": "b", "fail_count": 3,
                     "success_count": 1, "heal_rate": 0.25},
                    {"actor": "c", "action": "d", "fail_count": 5,
                     "success_count": 2, "heal_rate": 0.29},
                ],
            }
            lens._calibrate_anomaly_thresholds(analysis)
            assert captured.get("heal_dist") == [3.0, 5.0]
        finally:
            _mod._llm_anomaly_thresholds = original


class TestProposeUsesConfiguredConstants:
    """propose() must not embed hardcoded magic numbers — all threshold references
    must use the module-level constants derived from args/lens_workflow_patterns.yaml."""

    def _make_lens(self):
        return WorkflowPatternLens()

    def _heal_pred(self, heal_rate: float):
        from tools.oracle.base_lens import OraclePrediction
        return OraclePrediction(
            lens="workflow_pattern",
            title="Self-Healing Candidate: actor / 'action'",
            description="",
            confidence=0.5,
            severity="info",
            category="self_healing_candidate",
            data={
                "actor": "builder",
                "action": "compile",
                "fail_count": 3,
                "success_count": 2,
                "heal_rate": heal_rate,
            },
        )

    def test_below_threshold_message_uses_constant_not_literal(self):
        """When heal_rate < _HEAL_REFLEX_MIN_RATE the recommendation must not
        contain the literal string '0.7' — it must interpolate the constant."""
        lens = self._make_lens()
        pred = self._heal_pred(heal_rate=0.0)  # well below threshold
        result = lens.propose([pred])
        recs = result[0].recommendations
        # The second recommendation is the conditional one
        below_msg = recs[1]
        # Must NOT contain a raw literal '0.7'
        assert "0.7" not in below_msg, (
            f"Hardcoded '0.7' found in recommendation: {below_msg!r}"
        )
        # Must reference the configured threshold value
        expected_pct = f"{_HEAL_REFLEX_MIN_RATE:.0%}"
        assert expected_pct in below_msg, (
            f"Expected '{expected_pct}' in recommendation: {below_msg!r}"
        )

    def test_above_threshold_message_uses_constant(self):
        """When heal_rate >= _HEAL_REFLEX_MIN_RATE the recommendation uses
        the formatted constant too (regression guard)."""
        lens = self._make_lens()
        pred = self._heal_pred(heal_rate=1.0)  # well above threshold
        result = lens.propose([pred])
        recs = result[0].recommendations
        above_msg = recs[1]
        expected_pct = f"{_HEAL_REFLEX_MIN_RATE:.0%}"
        assert expected_pct in above_msg


class TestAutomationHighRatio:
    """_automation_potential uses _AUTOMATION_HIGH_RATIO (configurable) instead of // 2."""

    def test_default_automation_high_ratio(self):
        assert _AUTOMATION_HIGH_RATIO == pytest.approx(0.5)

    def test_all_automated_events_is_high(self):
        pattern = ("code_generated", "test_executed", "test_passed")
        assert _automation_potential(pattern) == "high"

    def test_manual_event_forces_low(self):
        pattern = ("code_generated", "approval_granted", "test_passed")
        assert _automation_potential(pattern) == "low"

    def test_no_matching_events_is_medium(self):
        pattern = ("unknown_event_a", "unknown_event_b", "unknown_event_c")
        assert _automation_potential(pattern) == "medium"

    def test_exactly_half_automated_is_high(self):
        # 2 out of 4 automated → ratio = 0.5 >= _AUTOMATION_HIGH_RATIO → high
        pattern = ("code_generated", "test_executed", "unknown_a", "unknown_b")
        assert _automation_potential(pattern) == "high"

    def test_below_half_automated_is_medium(self):
        # 1 out of 4 automated → ratio = 0.25 < 0.5 → medium
        pattern = ("code_generated", "unknown_a", "unknown_b", "unknown_c")
        assert _automation_potential(pattern) == "medium"

    def test_empty_pattern_is_medium(self):
        assert _automation_potential(()) == "medium"


class TestMaxKanbanDone:
    """_MAX_KANBAN_DONE is loaded from config and replaces the hardcoded LIMIT 500."""

    def test_default_max_kanban_done(self):
        assert _MAX_KANBAN_DONE == 500

    def test_load_kanban_done_respects_limit(self):
        """_load_kanban_done must pass _MAX_KANBAN_DONE as the LIMIT parameter."""
        import sqlite3
        import tools.oracle.lenses.lens_workflow_patterns as _mod

        original_limit = _mod._MAX_KANBAN_DONE
        _mod._MAX_KANBAN_DONE = 3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE kanban_tasks "
            "(id TEXT, title TEXT, task_type TEXT, priority TEXT, "
            "created_at TEXT, completed_at TEXT, status TEXT)"
        )
        for i in range(10):
            conn.execute(
                "INSERT INTO kanban_tasks VALUES (?, ?, 'feature', 'high', '2026-01-01', '2026-01-02', 'done')",
                (str(i), f"Task {i}"),
            )
        conn.commit()

        data: dict = {}
        try:
            lens = WorkflowPatternLens()
            lens._load_kanban_done(conn, data)
        finally:
            _mod._MAX_KANBAN_DONE = original_limit
            conn.close()

        assert len(data["kanban_done"]) == 3


class TestConfigurableEventTypes:
    """automated_event_types and manual_event_types are loaded from config, not hardcoded."""

    def test_automated_event_types_is_frozenset(self):
        assert isinstance(_AUTOMATED_EVENT_TYPES, frozenset)

    def test_manual_event_types_is_frozenset(self):
        assert isinstance(_MANUAL_EVENT_TYPES, frozenset)

    def test_default_automated_contains_code_generated(self):
        assert "code_generated" in _AUTOMATED_EVENT_TYPES

    def test_default_automated_contains_test_executed(self):
        assert "test_executed" in _AUTOMATED_EVENT_TYPES

    def test_default_manual_contains_approval_granted(self):
        assert "approval_granted" in _MANUAL_EVENT_TYPES

    def test_automation_potential_respects_configured_automated_types(self):
        """When a module-level override changes _AUTOMATED_EVENT_TYPES, _automation_potential reflects it."""
        import tools.oracle.lenses.lens_workflow_patterns as _mod

        original = _mod._AUTOMATED_EVENT_TYPES
        _mod._AUTOMATED_EVENT_TYPES = frozenset({"custom_event"})
        try:
            # custom_event alone → ratio = 1.0 >= 0.5 → high
            result = _mod._automation_potential(("custom_event",))
            assert result == "high"
        finally:
            _mod._AUTOMATED_EVENT_TYPES = original

    def test_automation_potential_respects_configured_manual_types(self):
        """When a module-level override changes _MANUAL_EVENT_TYPES, manual events force 'low'."""
        import tools.oracle.lenses.lens_workflow_patterns as _mod

        original = _mod._MANUAL_EVENT_TYPES
        _mod._MANUAL_EVENT_TYPES = frozenset({"custom_manual"})
        try:
            result = _mod._automation_potential(("code_generated", "custom_manual"))
            assert result == "low"
        finally:
            _mod._MANUAL_EVENT_TYPES = original


class TestMinPercentileRankPopulation:
    """min_percentile_rank_population is loaded from config, not hardcoded as 2."""

    def test_default_value(self):
        assert _MIN_PERCENTILE_RANK_POPULATION == 2

    def test_population_below_min_returns_one(self):
        """A single-element population is below the default minimum — returns 1.0."""
        assert _percentile_rank(5.0, [3.0]) == 1.0

    def test_population_at_min_computes_rank(self):
        """A two-element population meets the default minimum — returns computed rank."""
        result = _percentile_rank(1.0, [1.0, 2.0])
        assert result == pytest.approx(0.5)

    def test_respects_overridden_min(self):
        """When _MIN_PERCENTILE_RANK_POPULATION is raised, previously-valid populations fall back."""
        import tools.oracle.lenses.lens_workflow_patterns as _mod

        original = _mod._MIN_PERCENTILE_RANK_POPULATION
        _mod._MIN_PERCENTILE_RANK_POPULATION = 5
        try:
            # 3-element population is now below minimum → should return 1.0
            result = _mod._percentile_rank(2.0, [1.0, 2.0, 3.0])
            assert result == 1.0
        finally:
            _mod._MIN_PERCENTILE_RANK_POPULATION = original
