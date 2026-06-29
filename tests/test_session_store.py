# CUI // SP-CTI
"""Tests for agent loop session checkpoint store (icdev/tools/llm/session_store.py)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


class TestSaveCheckpoint:
    def test_saves_messages_as_json(self, mock_conn):
        from icdev.tools.llm import session_store
        messages = [{"role": "user", "content": "hello"}]
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.save_checkpoint("sess-1", 3, messages)
        call_args = mock_conn.execute.call_args[0]
        params = call_args[1]
        # Find the JSON string in params
        json_strs = [a for a in params if isinstance(a, str) and a.startswith("[")]
        assert len(json_strs) == 1
        assert json.loads(json_strs[0]) == messages

    def test_saves_session_id_and_turn(self, mock_conn):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.save_checkpoint("sess-42", 7, [])
        params = mock_conn.execute.call_args[0][1]
        assert "sess-42" in params
        assert 7 in params

    def test_saves_parent_session_id(self, mock_conn):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.save_checkpoint("sess-2", 1, [], parent_session_id="parent-99")
        params = mock_conn.execute.call_args[0][1]
        assert "parent-99" in params

    def test_non_fatal_on_db_error(self):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", side_effect=Exception("db down")):
            # Must not raise
            session_store.save_checkpoint("sess-3", 1, [])

    def test_saves_cost_fields(self, mock_conn):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.save_checkpoint(
                "s", 1, [],
                input_tokens=100, output_tokens=200, cost_usd=0.05,
            )
        params = mock_conn.execute.call_args[0][1]
        assert 100 in params
        assert 200 in params
        assert 0.05 in params

    def test_saves_model_and_provider(self, mock_conn):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.save_checkpoint(
                "s", 0, [], model_id="claude-sonnet-4-6", provider="anthropic",
            )
        params = mock_conn.execute.call_args[0][1]
        assert "claude-sonnet-4-6" in params
        assert "anthropic" in params

    def test_uses_context_manager_on_conn(self):
        from icdev.tools.llm import session_store
        entered = []
        conn = MagicMock()
        conn.__enter__ = lambda s: entered.append(1) or s
        conn.__exit__ = MagicMock(return_value=False)
        with patch.object(session_store, "_conn", return_value=conn):
            session_store.save_checkpoint("s", 0, [])
        assert entered, "context manager __enter__ was not called"


class TestLoadCheckpoint:
    def test_returns_none_when_missing(self, mock_conn):
        from icdev.tools.llm import session_store
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch.object(session_store, "_conn", return_value=mock_conn):
            result = session_store.load_checkpoint("nonexistent")
        assert result is None

    def test_returns_parsed_messages(self, mock_conn):
        from icdev.tools.llm import session_store
        messages = [{"role": "assistant", "content": "hi"}]
        mock_conn.execute.return_value.fetchone.return_value = (
            json.dumps(messages), 5, "parent-1", "claude-3", "anthropic", 50, 100, 0.02,
        )
        with patch.object(session_store, "_conn", return_value=mock_conn):
            result = session_store.load_checkpoint("sess-abc")
        assert result is not None
        assert result["messages"] == messages
        assert result["turn_number"] == 5
        assert result["parent_session_id"] == "parent-1"
        assert result["model_id"] == "claude-3"
        assert result["provider"] == "anthropic"
        assert result["input_tokens"] == 50
        assert result["output_tokens"] == 100
        assert abs(result["cost_usd"] - 0.02) < 1e-9

    def test_non_fatal_on_db_error(self):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", side_effect=Exception("db down")):
            result = session_store.load_checkpoint("sess-x")
        assert result is None

    def test_returns_defaults_for_null_optional_fields(self, mock_conn):
        from icdev.tools.llm import session_store
        mock_conn.execute.return_value.fetchone.return_value = (
            "[]", 0, None, None, None, None, None, None,
        )
        with patch.object(session_store, "_conn", return_value=mock_conn):
            result = session_store.load_checkpoint("sess-def")
        assert result is not None
        assert result["parent_session_id"] == ""
        assert result["model_id"] == ""
        assert result["provider"] == ""
        assert result["input_tokens"] == 0
        assert result["cost_usd"] == 0.0

    def test_passes_session_id_as_query_param(self, mock_conn):
        from icdev.tools.llm import session_store
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.load_checkpoint("target-id")
        call_params = mock_conn.execute.call_args[0][1]
        assert call_params == ("target-id",)


class TestDeleteCheckpoint:
    def test_executes_delete_sql(self, mock_conn):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.delete_checkpoint("sess-del")
        sql = mock_conn.execute.call_args[0][0]
        assert "DELETE" in sql.upper()

    def test_passes_session_id_to_delete(self, mock_conn):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.delete_checkpoint("to-remove")
        params = mock_conn.execute.call_args[0][1]
        assert "to-remove" in params

    def test_non_fatal_on_db_error(self):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", side_effect=Exception("gone")):
            session_store.delete_checkpoint("x")  # must not raise


class TestListCheckpoints:
    def test_returns_list_of_dicts(self, mock_conn):
        from icdev.tools.llm import session_store
        mock_conn.execute.return_value.fetchall.return_value = [
            ("s1", "", 2, "m1", "p1", 10, 20, 0.01, "2026-01-01T00:00:00"),
        ]
        with patch.object(session_store, "_conn", return_value=mock_conn):
            result = session_store.list_checkpoints(limit=5)
        assert len(result) == 1
        assert result[0]["session_id"] == "s1"
        assert result[0]["turn_number"] == 2

    def test_passes_limit_param(self, mock_conn):
        from icdev.tools.llm import session_store
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch.object(session_store, "_conn", return_value=mock_conn):
            session_store.list_checkpoints(limit=25)
        params = mock_conn.execute.call_args[0][1]
        assert 25 in params

    def test_non_fatal_on_db_error(self):
        from icdev.tools.llm import session_store
        with patch.object(session_store, "_conn", side_effect=Exception("gone")):
            result = session_store.list_checkpoints()
        assert result == []

    def test_empty_table_returns_empty_list(self, mock_conn):
        from icdev.tools.llm import session_store
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch.object(session_store, "_conn", return_value=mock_conn):
            result = session_store.list_checkpoints()
        assert result == []
