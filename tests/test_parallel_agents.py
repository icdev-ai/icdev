# CUI // SP-CTI
"""Tests for AgentToolRegistry.parallel_agents tool."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_spec(namespace="test-ns", folder_access=None):
    return SimpleNamespace(
        coworker_id="test-cw",
        trust_tier="green",
        folder_access=folder_access or [],
        icdev_tools=[],
        coordination_namespace=namespace,
    )


def _make_registry(namespace="test-ns"):
    from icdev.tools.ace.agent_tools import AgentToolRegistry

    return AgentToolRegistry(spec=_make_spec(namespace), instance_id="test-inst")


class TestParallelAgentsTool:
    """parallel_agents is registered in _SCHEMAS and accessible via build()."""

    def test_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS

        assert "parallel_agents" in _SCHEMAS

    def test_schema_has_required_tasks(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS

        params = _SCHEMAS["parallel_agents"]["function"]["parameters"]
        assert "tasks" in params["required"]

    def test_schema_is_read_only(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS

        assert _SCHEMAS["parallel_agents"].get("is_read_only") is True

    def test_build_includes_parallel_agents(self):
        reg = _make_registry()
        tools, handlers = reg.build(["parallel_agents", "done"])
        names = [t["function"]["name"] for t in tools]
        assert "parallel_agents" in names
        assert "parallel_agents" in handlers

    def test_make_handler_returns_callable(self):
        reg = _make_registry()
        handler = reg._make_handler("parallel_agents")
        assert callable(handler)

    def test_make_handler_unknown_name_returns_none(self):
        reg = _make_registry()
        assert reg._make_handler("nonexistent_tool_xyz") is None


class TestParallelAgentsHandler:
    """Handler logic: fan-out, error handling, result ordering."""

    def _run(self, inp, registry=None, stop=None):
        if registry is None:
            registry = _make_registry()
        return registry._parallel_agents(inp, stop or threading.Event())

    def test_empty_tasks_returns_error(self):
        result = self._run({"tasks": []})
        assert "error" in result.lower() or "required" in result.lower()

    def test_missing_tasks_key_returns_error(self):
        result = self._run({})
        assert "error" in result.lower()

    def test_too_many_tasks_returns_error(self):
        tasks = [{"key": f"t{i}", "task": "do something"} for i in range(9)]
        result = self._run({"tasks": tasks})
        assert "max 8" in result.lower() or "error" in result.lower()

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_single_task_succeeds(self, mock_loop):
        mock_result = MagicMock()
        mock_result.final_content = "sub-agent done: task completed"
        mock_result.done = True
        mock_result.turns = 2
        mock_result.result_subtype = "success"
        mock_result.session_id = "sess-abc"
        mock_loop.return_value = mock_result

        reg = _make_registry()
        result = reg._parallel_agents(
            {"tasks": [{"key": "alpha", "task": "check file structure"}]},
            threading.Event(),
        )
        assert "alpha" in result

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_multiple_tasks_all_run(self, mock_loop):
        mock_result = MagicMock()
        mock_result.final_content = "done"
        mock_result.done = True
        mock_result.turns = 1
        mock_result.result_subtype = "success"
        mock_result.session_id = "sess-x"
        mock_loop.return_value = mock_result

        reg = _make_registry()
        tasks = [
            {"key": "t1", "task": "do task one"},
            {"key": "t2", "task": "do task two"},
            {"key": "t3", "task": "do task three"},
        ]
        result = reg._parallel_agents({"tasks": tasks}, threading.Event())
        assert "t1" in result
        assert "t2" in result
        assert "t3" in result

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_results_header_shows_count(self, mock_loop):
        mock_result = MagicMock()
        mock_result.final_content = "ok"
        mock_result.done = True
        mock_result.turns = 1
        mock_result.result_subtype = "success"
        mock_result.session_id = "sess-y"
        mock_loop.return_value = mock_result

        reg = _make_registry()
        result = reg._parallel_agents(
            {"tasks": [{"key": "a", "task": "x"}, {"key": "b", "task": "y"}]},
            threading.Event(),
        )
        assert "2" in result

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_failed_sub_agent_shows_error(self, mock_loop):
        mock_loop.side_effect = RuntimeError("sub-agent exploded")

        reg = _make_registry()
        result = reg._parallel_agents(
            {"tasks": [{"key": "boom", "task": "do dangerous thing"}]},
            threading.Event(),
        )
        assert "ERROR" in result or "error" in result.lower()
        assert "boom" in result

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_partial_failure_others_succeed(self, mock_loop):
        ok_result = MagicMock()
        ok_result.final_content = "success output"
        ok_result.done = True
        ok_result.turns = 1
        ok_result.result_subtype = "success"
        ok_result.session_id = "sess-z"

        call_count = [0]

        def _side(router, *, user_prompt, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first task failed")
            return ok_result

        mock_loop.side_effect = _side

        reg = _make_registry()
        result = reg._parallel_agents(
            {
                "tasks": [
                    {"key": "bad", "task": "fail task"},
                    {"key": "good", "task": "succeed task"},
                ]
            },
            threading.Event(),
        )
        assert "bad" in result
        assert "good" in result

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_result_order_preserved(self, mock_loop):
        def _side(router, *, user_prompt, **kwargs):
            r = MagicMock()
            r.final_content = f"result for: {user_prompt[:20]}"
            r.done = True
            r.turns = 1
            r.result_subtype = "success"
            r.session_id = "sess-ord"
            return r

        mock_loop.side_effect = _side

        reg = _make_registry()
        tasks = [{"key": f"task_{i}", "task": f"do task {i}"} for i in range(4)]
        result = reg._parallel_agents({"tasks": tasks}, threading.Event())
        positions = [result.find(f"task_{i}") for i in range(4)]
        assert positions == sorted(positions), f"Out of order: {positions}"

    def test_missing_key_in_task_handled(self):
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        with patch.object(AgentToolRegistry, "_spawn_agent", return_value="ok", create=True):
            reg = _make_registry()
            result = reg._parallel_agents(
                {"tasks": [{"task": "no key here"}]},
                threading.Event(),
            )
            assert isinstance(result, str)
            assert "error" in result.lower() or "key" in result.lower()

    def test_missing_task_text_handled(self):
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        with patch.object(AgentToolRegistry, "_spawn_agent", return_value="ok", create=True):
            reg = _make_registry()
            result = reg._parallel_agents(
                {"tasks": [{"key": "k"}]},
                threading.Event(),
            )
            assert isinstance(result, str)
            assert "error" in result.lower()

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_max_8_tasks_accepted(self, mock_loop):
        mock_result = MagicMock()
        mock_result.final_content = "ok"
        mock_result.done = True
        mock_result.turns = 1
        mock_result.result_subtype = "success"
        mock_result.session_id = "sess-max"
        mock_loop.return_value = mock_result

        reg = _make_registry()
        tasks = [{"key": f"t{i}", "task": f"task {i}"} for i in range(8)]
        result = reg._parallel_agents({"tasks": tasks}, threading.Event())
        assert "error" not in result.lower().split("\n")[0]

    @patch("icdev.tools.ace.agent_tools.run_agent_loop")
    def test_9_tasks_rejected(self, mock_loop):
        reg = _make_registry()
        tasks = [{"key": f"t{i}", "task": f"task {i}"} for i in range(9)]
        result = reg._parallel_agents({"tasks": tasks}, threading.Event())
        assert "error" in result.lower()
        mock_loop.assert_not_called()
