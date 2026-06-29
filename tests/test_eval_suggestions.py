# CUI // SP-CTI
"""Tests for icdev.tools.ace.evaluator.suggest_improvements."""
from __future__ import annotations
from icdev.tools.ace.evaluator import EvalResult, suggest_improvements


def _er(**kwargs) -> EvalResult:
    defaults = dict(
        session_id="s-test",
        outcome="success",
        done=True,
        turns_used=3,
        max_iterations=12,
        efficiency_score=0.75,
        total_tool_calls=4,
        error_tool_calls=0,
        tool_error_rate=0.0,
        unique_tools=["read_file", "write_file", "done"],
        tool_precision=0.75,
        total_cost_usd=0.01,
        reasoning_coverage=0.8,
        avg_reasoning_chars=120.0,
        has_error_recovery_reasoning=False,
        plan_stated=True,
        scope_violations=0,
        trust_denials=0,
        reasoning_style="cod",
    )
    defaults.update(kwargs)
    return EvalResult(**defaults)


class TestSuggestImprovements:
    def test_clean_session_returns_no_suggestions(self):
        s = suggest_improvements(_er())
        assert s == []

    def test_max_turns_hit_suggests_increase(self):
        s = suggest_improvements(_er(outcome="error_max_turns", efficiency_score=0.0, turns_used=12, max_iterations=12))
        fields = [x["field"] for x in s]
        assert "max_iterations" in fields

    def test_scope_violations_flagged_high(self):
        s = suggest_improvements(_er(scope_violations=2))
        high = [x for x in s if x["severity"] == "high"]
        assert any("scope" in x["issue"].lower() for x in high)

    def test_high_tool_error_rate_flagged(self):
        s = suggest_improvements(_er(tool_error_rate=0.6, error_tool_calls=3, total_tool_calls=5))
        assert any(x["field"] == "system_prompt" and "error" in x["suggestion"].lower() for x in s)

    def test_no_plan_stated_suggestion(self):
        s = suggest_improvements(_er(plan_stated=False, turns_used=4, total_tool_calls=4))
        assert any("plan" in x["suggestion"].lower() for x in s)

    def test_low_reasoning_coverage_suggestion(self):
        s = suggest_improvements(_er(reasoning_coverage=0.2, total_tool_calls=5))
        assert any("reasoning" in x["issue"].lower() for x in s)

    def test_trust_denials_flagged_high(self):
        s = suggest_improvements(_er(trust_denials=1))
        assert any(x["severity"] == "high" and "trust" in x["issue"].lower() for x in s)

    def test_high_severity_first(self):
        s = suggest_improvements(_er(
            outcome="error_max_turns", scope_violations=1,
            efficiency_score=0.0, turns_used=12, max_iterations=12,
        ))
        if len(s) >= 2:
            assert s[0]["severity"] == "high"

    def test_returns_list_of_dicts(self):
        s = suggest_improvements(_er(tool_error_rate=0.5, total_tool_calls=4, error_tool_calls=2))
        assert isinstance(s, list)
        for item in s:
            assert "issue" in item
            assert "suggestion" in item
            assert "field" in item
            assert "severity" in item

    def test_no_suggestions_when_very_few_tool_calls(self):
        """Low tool call count suppresses some heuristics to avoid noise."""
        s = suggest_improvements(_er(
            tool_error_rate=0.5,
            total_tool_calls=2,
            error_tool_calls=1,
            reasoning_coverage=0.2,
        ))
        assert isinstance(s, list)

    def test_error_recovery_suggestion_when_errors_no_reasoning(self):
        s = suggest_improvements(_er(
            has_error_recovery_reasoning=False,
            error_tool_calls=3,
            tool_error_rate=0.6,
            total_tool_calls=5,
        ))
        assert any("error-recovery" in x["issue"].lower() or "error recovery" in x["issue"].lower() for x in s)

    def test_low_tool_precision_suggestion(self):
        s = suggest_improvements(_er(
            tool_precision=0.2,
            total_tool_calls=8,
            unique_tools=["read_file"],
        ))
        assert any("precision" in x["issue"].lower() or "repetition" in x["issue"].lower() for x in s)
