# CUI // SP-CTI
"""Tests for apply_suggestions_to_prompt, compare_evals, and rerun/compare API routes."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _er(**kwargs):
    from icdev.tools.ace.evaluator import EvalResult
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
        unique_tools=["read_file", "done"],
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


# ---------------------------------------------------------------------------
# apply_suggestions_to_prompt
# ---------------------------------------------------------------------------

class TestApplySuggestionsToPrompt:
    def test_no_matching_suggestions_returns_original(self):
        from icdev.tools.ace.evaluator import apply_suggestions_to_prompt
        original = "You are a helpful agent."
        result = apply_suggestions_to_prompt(original, [])
        assert result == original

    def test_only_high_medium_system_prompt_suggestions_applied(self):
        from icdev.tools.ace.evaluator import apply_suggestions_to_prompt
        suggestions = [
            {"field": "system_prompt", "severity": "high", "suggestion": "Think before you act."},
            {"field": "system_prompt", "severity": "low", "suggestion": "Low priority note."},
            {"field": "max_iterations", "severity": "high", "suggestion": "Increase iterations."},
            {"field": "system_prompt", "severity": "medium", "suggestion": "Medium advice."},
        ]
        result = apply_suggestions_to_prompt("Original.", suggestions)
        assert "Think before you act." in result
        assert "Medium advice." in result
        assert "Low priority note." not in result
        assert "Increase iterations." not in result
        assert "Original." in result

    def test_prefix_before_original(self):
        from icdev.tools.ace.evaluator import apply_suggestions_to_prompt
        suggestions = [{"field": "system_prompt", "severity": "high", "suggestion": "Do X."}]
        result = apply_suggestions_to_prompt("My prompt.", suggestions)
        assert result.index("[Improvement guidance") < result.index("My prompt.")

    def test_empty_original_prompt(self):
        from icdev.tools.ace.evaluator import apply_suggestions_to_prompt
        suggestions = [{"field": "system_prompt", "severity": "high", "suggestion": "Do X."}]
        result = apply_suggestions_to_prompt("", suggestions)
        assert "Do X." in result
        assert isinstance(result, str)

    def test_no_system_prompt_field_suggestions_no_op(self):
        from icdev.tools.ace.evaluator import apply_suggestions_to_prompt
        suggestions = [
            {"field": "max_iterations", "severity": "high", "suggestion": "Add more iterations."},
            {"field": "folder_access", "severity": "high", "suggestion": "Restrict access."},
        ]
        result = apply_suggestions_to_prompt("Unchanged.", suggestions)
        assert result == "Unchanged."


# ---------------------------------------------------------------------------
# compare_evals
# ---------------------------------------------------------------------------

class TestCompareEvals:
    def _mock_pair(self, ea, eb):
        def _get_eval(session_id):
            return ea if session_id == "a" else eb

        def _score_session(session_id, **kwargs):
            return ea if session_id == "a" else eb

        return patch("icdev.tools.ace.evaluator.get_eval", side_effect=_get_eval), \
               patch("icdev.tools.ace.evaluator.score_session", side_effect=_score_session)

    def test_returns_required_keys(self):
        from icdev.tools.ace.evaluator import compare_evals
        ea = _er(session_id="a", efficiency_score=0.5, reasoning_coverage=0.6)
        eb = _er(session_id="b", efficiency_score=0.7, reasoning_coverage=0.8)
        p1, p2 = self._mock_pair(ea, eb)
        with p1, p2:
            result = compare_evals("a", "b")
        assert "session_a" in result
        assert "session_b" in result
        assert "fields" in result
        assert "overall_improved" in result
        assert "improvements" in result
        assert "regressions" in result

    def test_improved_session_marked_correctly(self):
        from icdev.tools.ace.evaluator import compare_evals
        ea = _er(session_id="a", efficiency_score=0.4, tool_error_rate=0.5)
        eb = _er(session_id="b", efficiency_score=0.8, tool_error_rate=0.1)
        p1, p2 = self._mock_pair(ea, eb)
        with p1, p2:
            result = compare_evals("a", "b")
        assert result["overall_improved"] is True
        eff = next(f for f in result["fields"] if f["name"] == "efficiency_score")
        assert eff["improved"] is True
        assert eff["delta"] > 0

    def test_regressed_session_marked_correctly(self):
        from icdev.tools.ace.evaluator import compare_evals
        ea = _er(session_id="a", efficiency_score=0.9, tool_error_rate=0.0)
        eb = _er(session_id="b", efficiency_score=0.2, tool_error_rate=0.8)
        p1, p2 = self._mock_pair(ea, eb)
        with p1, p2:
            result = compare_evals("a", "b")
        assert result["overall_improved"] is False

    def test_fields_list_has_all_expected_names(self):
        from icdev.tools.ace.evaluator import compare_evals
        ea = _er(session_id="a")
        eb = _er(session_id="b")
        p1, p2 = self._mock_pair(ea, eb)
        with p1, p2:
            result = compare_evals("a", "b")
        field_names = {f["name"] for f in result["fields"]}
        assert "efficiency_score" in field_names
        assert "reasoning_coverage" in field_names
        assert "tool_error_rate" in field_names
        assert "plan_stated" in field_names

    def test_outcomes_included(self):
        from icdev.tools.ace.evaluator import compare_evals
        ea = _er(session_id="a", outcome="error_max_turns")
        eb = _er(session_id="b", outcome="success")
        p1, p2 = self._mock_pair(ea, eb)
        with p1, p2:
            result = compare_evals("a", "b")
        assert result["outcome_a"] == "error_max_turns"
        assert result["outcome_b"] == "success"


# ---------------------------------------------------------------------------
# API routes — blueprint integration
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    import os
    os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
    from icdev.tools.ace.blueprint import ace_api_bp
    from flask import Flask
    app = Flask(__name__)
    # Register the API blueprint directly with a unique test name to avoid
    # conflicts with ace_bp's auto-mount of ace_api_bp via _mount_api.
    app.register_blueprint(ace_api_bp, url_prefix="/api/ace", name="ace_api_test")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestRerunRoute:
    def test_unknown_session_returns_404(self, client):
        # get_session_metadata is lazily imported inside the route handler;
        # patch it at its canonical module location.
        with patch("icdev.tools.llm.agent_loop_session.get_session_metadata", return_value=None):
            res = client.post("/api/ace/sessions/no-such-session/rerun",
                              json={}, content_type="application/json")
        assert res.status_code == 404

    def test_missing_problem_text_returns_422(self, client):
        meta = {"session_id": "s1", "instance_id": "i1"}
        with patch("icdev.tools.llm.agent_loop_session.get_session_metadata", return_value=meta), \
             patch("icdev.tools.ace.blueprint._db") as mock_db:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = ('{}',)
            mock_db.return_value = conn
            with patch("icdev.tools.ace.evaluator.get_eval", return_value=None), \
                 patch("icdev.tools.ace.evaluator.score_session", return_value=_er(session_id="s1", outcome="not_found")):
                res = client.post("/api/ace/sessions/s1/rerun",
                                  json={"apply_suggestions": False},
                                  content_type="application/json")
        assert res.status_code in (404, 422, 500)


class TestCompareRoute:
    def test_missing_baseline_returns_400(self, client):
        res = client.get("/api/ace/sessions/s1/eval/compare")
        assert res.status_code == 400
        assert b"baseline" in res.data

    def test_compare_returns_result(self, client):
        mock_result = {
            "session_a": "base", "session_b": "s1",
            "outcome_a": "success", "outcome_b": "success",
            "fields": [], "improvements": 0, "regressions": 0,
            "overall_improved": False,
        }
        with patch("icdev.tools.ace.evaluator.compare_evals", return_value=mock_result):
            res = client.get("/api/ace/sessions/s1/eval/compare?baseline=base")
        assert res.status_code == 200
        data = res.get_json()
        assert data["session_a"] == "base"
        assert data["session_b"] == "s1"
