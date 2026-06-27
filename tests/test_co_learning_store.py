# CUI // SP-CTI
"""Tests for co-learning persistence store."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch, call


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.fetchone.return_value = None
    return conn


# ---------------------------------------------------------------------------
# record_session_suggestions
# ---------------------------------------------------------------------------

class TestRecordSessionSuggestions:
    def test_empty_list_returns_zero(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            count = co_learning_store.record_session_suggestions("s1", "ai_developer", [])
        assert count == 0
        mock_conn.execute.assert_not_called()

    def test_valid_suggestions_persisted(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        suggestions = [
            {"field": "system_prompt", "suggestion": "State assumptions first", "severity": "high"},
            {"field": "reasoning_style", "suggestion": "Use step-by-step reasoning", "severity": "medium"},
        ]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            count = co_learning_store.record_session_suggestions("s1", "ai_developer", suggestions)
        assert count == 2
        assert mock_conn.execute.call_count == 2

    def test_uses_field_key_for_category(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        suggestions = [{"field": "max_iterations", "suggestion": "Increase limit", "severity": "high"}]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            co_learning_store.record_session_suggestions("s1", "ai_developer", suggestions)
        sql_params = mock_conn.execute.call_args[0][1]
        assert "max_iterations" in sql_params

    def test_falls_back_to_category_key(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        suggestions = [{"category": "system_prompt", "suggestion": "Test suggestion", "severity": "low"}]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            count = co_learning_store.record_session_suggestions("s1", "ai_developer", suggestions)
        assert count == 1

    def test_skips_empty_suggestion_text(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        suggestions = [{"field": "system_prompt", "suggestion": ""}]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            count = co_learning_store.record_session_suggestions("s1", "ai_developer", suggestions)
        assert count == 0

    def test_db_error_returns_zero_no_raise(self):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", side_effect=Exception("db down")):
            count = co_learning_store.record_session_suggestions(
                "s1", "ai_developer",
                [{"field": "system_prompt", "suggestion": "x", "severity": "medium"}],
            )
        assert count == 0

    def test_severity_stored(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        suggestions = [{"field": "system_prompt", "suggestion": "foo", "severity": "high"}]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            co_learning_store.record_session_suggestions("s1", "ai_developer", suggestions)
        params = mock_conn.execute.call_args[0][1]
        assert "high" in params


# ---------------------------------------------------------------------------
# build_system_prompt_patch
# ---------------------------------------------------------------------------

class TestBuildSystemPromptPatch:
    def test_returns_empty_when_no_rows(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            result = co_learning_store.build_system_prompt_patch("ai_developer")
        assert result == ""

    def test_returns_patch_with_colearning_header(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_conn.execute.return_value.fetchall.return_value = [
            ("State assumptions before coding", "system_prompt", 3),
        ]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            result = co_learning_store.build_system_prompt_patch("ai_developer")
        assert "Co-Learning Note" in result
        assert "State assumptions before coding" in result
        assert "ai_developer" in result

    def test_includes_bullet_per_suggestion(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_conn.execute.return_value.fetchall.return_value = [
            ("Tip 1", "system_prompt", 2),
            ("Tip 2", "reasoning_style", 1),
        ]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            result = co_learning_store.build_system_prompt_patch("qa_manager")
        assert "- Tip 1" in result
        assert "- Tip 2" in result

    def test_default_categories_filter(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            co_learning_store.build_system_prompt_patch("ai_developer")
        sql = mock_conn.execute.call_args[0][0]
        assert "category IN" in sql
        params = mock_conn.execute.call_args[0][1]
        assert "system_prompt" in params
        assert "reasoning_style" in params

    def test_custom_categories_filter(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            co_learning_store.build_system_prompt_patch("ai_developer", categories=["max_iterations"])
        params = mock_conn.execute.call_args[0][1]
        assert "max_iterations" in params

    def test_db_error_returns_empty_string(self):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", side_effect=Exception("gone")):
            result = co_learning_store.build_system_prompt_patch("ai_developer")
        assert result == ""


# ---------------------------------------------------------------------------
# mark_applied / dismiss_suggestion
# ---------------------------------------------------------------------------

class TestMarkApplied:
    def test_executes_update(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            co_learning_store.mark_applied("ai_developer", "system_prompt")
        sql = mock_conn.execute.call_args[0][0]
        assert "UPDATE" in sql.upper()
        assert "applied_count" in sql
        params = mock_conn.execute.call_args[0][1]
        assert "ai_developer" in params
        assert "system_prompt" in params

    def test_non_fatal_on_error(self):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", side_effect=Exception("gone")):
            co_learning_store.mark_applied("ai_developer", "system_prompt")  # must not raise


class TestDismissSuggestion:
    def test_executes_update(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            co_learning_store.dismiss_suggestion("ai_developer", "State assumptions")
        sql = mock_conn.execute.call_args[0][0]
        assert "dismissed_count" in sql
        params = mock_conn.execute.call_args[0][1]
        assert "State assumptions" in params

    def test_non_fatal_on_error(self):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", side_effect=Exception("gone")):
            co_learning_store.dismiss_suggestion("r", "s")  # must not raise


# ---------------------------------------------------------------------------
# get_suggestions_for_role
# ---------------------------------------------------------------------------

class TestGetSuggestionsForRole:
    def test_returns_list_of_dicts(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_conn.execute.return_value.fetchall.return_value = [
            (1, "ai_developer", "system_prompt", "Tip A", "sess-1", "high", 3, 0, "2026-01-01", "2026-06-01"),
        ]
        with patch.object(co_learning_store, "_conn", return_value=mock_conn):
            result = co_learning_store.get_suggestions_for_role("ai_developer")
        assert len(result) == 1
        assert result[0]["session_id"] == "sess-1"
        assert result[0]["category"] == "system_prompt"

    def test_returns_empty_on_db_error(self):
        from icdev.tools.llm import co_learning_store
        with patch.object(co_learning_store, "_conn", side_effect=Exception("gone")):
            result = co_learning_store.get_suggestions_for_role("ai_developer")
        assert result == []


# ---------------------------------------------------------------------------
# auto_record_from_loop_result
# ---------------------------------------------------------------------------

class TestAutoRecord:
    def test_calls_score_suggest_record(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_eval = MagicMock()
        mock_suggestions = [{"field": "system_prompt", "suggestion": "test", "severity": "medium"}]

        with patch("icdev.tools.ace.evaluator.score_session", return_value=mock_eval) as mock_score, \
             patch("icdev.tools.ace.evaluator.suggest_improvements", return_value=mock_suggestions) as mock_suggest, \
             patch.object(co_learning_store, "_conn", return_value=mock_conn):
            from icdev.tools.llm.co_learning_store import auto_record_from_loop_result
            count = auto_record_from_loop_result("s1", "ai_developer", MagicMock())

        mock_score.assert_called_once_with("s1")
        mock_suggest.assert_called_once_with(mock_eval)
        assert count == 1

    def test_returns_zero_when_eval_is_none(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        with patch("icdev.tools.ace.evaluator.score_session", return_value=None):
            count = co_learning_store.auto_record_from_loop_result("s1", "ai_developer", MagicMock())
        assert count == 0

    def test_non_fatal_on_evaluator_error(self):
        from icdev.tools.llm import co_learning_store
        with patch("icdev.tools.ace.evaluator.score_session", side_effect=Exception("no db")):
            count = co_learning_store.auto_record_from_loop_result("s1", "ai_developer", MagicMock())
        assert count == 0

    def test_returns_zero_when_suggestions_empty(self, mock_conn):
        from icdev.tools.llm import co_learning_store
        mock_eval = MagicMock()
        with patch("icdev.tools.ace.evaluator.score_session", return_value=mock_eval), \
             patch("icdev.tools.ace.evaluator.suggest_improvements", return_value=[]), \
             patch.object(co_learning_store, "_conn", return_value=mock_conn):
            count = co_learning_store.auto_record_from_loop_result("s1", "ai_developer", MagicMock())
        assert count == 0
