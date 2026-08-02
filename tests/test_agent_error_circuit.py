# CUI // SP-CTI
"""Tests for the error-loop circuit breaker in run_agent_loop().

When every tool call in N consecutive turns returns an error, the loop
aborts with ResultSubtype.error_consecutive_tool_failures.
"""
from __future__ import annotations

import pytest

from icdev.tools.llm.agent_loop import run_agent_loop, ResultSubtype, DONE


@pytest.fixture(autouse=True)
def _no_approval_gate(monkeypatch):
    """Opt out of the ars-appr-01 approval gate — see tests/test_approval_gate.py.

    These tests count *tool errors* to prove the circuit breaker trips. The gate
    correctly halts their synthetic tools for approval, which would add errors of
    a different kind and make the counts measure the gate instead of the breaker.
    """
    monkeypatch.setenv("ICDEV_AGENT_APPROVAL_GATE", "0")


# ---------------------------------------------------------------------------
# Minimal fake router (mirrors test_agent_loop.py pattern)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, tool_calls=None, content="done"):
        self.tool_calls = tool_calls or []
        self.content = content
        self.stop_reason = "end_turn" if not tool_calls else "tool_use"
        self.input_tokens = 10
        self.output_tokens = 5
        self.cost_usd = 0.001


class FakeRouter:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    def invoke(self, fn, req):
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return FakeResponse(content="final answer")

    def get_provider_for_function(self, fn):
        class FakeProv:
            provider_name = "fake"
        return FakeProv(), "fake-model", {}

    def get_model_config(self, fn):
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_error_tool():
    def handler(inp, stop):
        raise RuntimeError("tool broken")
    return handler


def _make_ok_tool():
    def handler(inp, stop):
        return "ok"
    return handler


def _tc(name="broken_tool"):
    return {"id": "tc-1", "name": name, "input": {}}


def _schema(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestErrorCircuitBreaker:
    def test_three_consecutive_error_turns_breaks_loop(self):
        router = FakeRouter([
            FakeResponse(tool_calls=[_tc()]),
            FakeResponse(tool_calls=[_tc()]),
            FakeResponse(tool_calls=[_tc()]),
            FakeResponse(content="should not reach here"),
        ])
        result = run_agent_loop(
            router,
            system_prompt="test",
            user_prompt="break things",
            tools=[_schema("broken_tool")],
            tool_handlers={"broken_tool": _make_error_tool()},
            max_consecutive_errors=3,
            max_iterations=10,
        )
        assert result.truncated
        assert result.result_subtype == ResultSubtype.error_consecutive_tool_failures

    def test_result_subtype_string_value(self):
        router = FakeRouter([FakeResponse(tool_calls=[_tc()])] * 3)
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("broken_tool")],
            tool_handlers={"broken_tool": _make_error_tool()},
            max_consecutive_errors=3, max_iterations=10,
        )
        assert result.result_subtype == "error_consecutive_tool_failures"

    def test_truncated_flag_set(self):
        router = FakeRouter([FakeResponse(tool_calls=[_tc()])] * 3)
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("broken_tool")],
            tool_handlers={"broken_tool": _make_error_tool()},
            max_consecutive_errors=3, max_iterations=10,
        )
        assert result.truncated is True

    def test_none_disables_guard(self):
        """max_consecutive_errors=None — 5 consecutive error turns, loop runs to max_iterations."""
        router = FakeRouter(
            [FakeResponse(tool_calls=[_tc()])] * 5
            + [FakeResponse(content="final")]
        )
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("broken_tool")],
            tool_handlers={"broken_tool": _make_error_tool()},
            max_consecutive_errors=None, max_iterations=6,
        )
        assert result.result_subtype != ResultSubtype.error_consecutive_tool_failures

    def test_success_turn_resets_counter(self):
        """Error, error, success, error, error → counter resets after success; no circuit break at 3."""
        call_count = [0]

        def alternating(inp, stop):
            call_count[0] += 1
            # calls 1,2 → error; call 3 → ok; calls 4,5 → error
            if call_count[0] in (1, 2, 4, 5):
                raise RuntimeError("fail")
            return "ok"

        router = FakeRouter([
            FakeResponse(tool_calls=[_tc("alt")]),  # error
            FakeResponse(tool_calls=[_tc("alt")]),  # error
            FakeResponse(tool_calls=[_tc("alt")]),  # success → resets
            FakeResponse(tool_calls=[_tc("alt")]),  # error
            FakeResponse(tool_calls=[_tc("alt")]),  # error
            FakeResponse(content="done"),
        ])
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("alt")],
            tool_handlers={"alt": alternating},
            max_consecutive_errors=3, max_iterations=10,
        )
        assert result.result_subtype != ResultSubtype.error_consecutive_tool_failures

    def test_custom_limit_five(self):
        """max_consecutive_errors=5 → breaks at turn 5, not 3."""
        router = FakeRouter([FakeResponse(tool_calls=[_tc()])] * 6)
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("broken_tool")],
            tool_handlers={"broken_tool": _make_error_tool()},
            max_consecutive_errors=5, max_iterations=10,
        )
        assert result.truncated
        assert result.turns >= 5

    def test_single_error_in_multi_tool_turn_does_not_count(self):
        """One error + one success in same turn → counter stays at 0."""
        call_count = [0]

        def half_error(inp, stop):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first call fails")
            return "ok"

        router = FakeRouter([
            FakeResponse(tool_calls=[_tc("ht"), _tc("ht")]),
            FakeResponse(content="done"),
        ])
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("ht")],
            tool_handlers={"ht": half_error},
            max_consecutive_errors=1, max_iterations=5,
        )
        assert result.result_subtype != ResultSubtype.error_consecutive_tool_failures

    def test_done_tool_in_error_turn_wins(self):
        """If done is signalled even when other tools error, loop exits cleanly."""
        def sometimes_error(inp, stop):
            raise RuntimeError("broken")

        def done_tool(inp, stop):
            return DONE

        router = FakeRouter([
            FakeResponse(tool_calls=[_tc("broken"), _tc("fin")]),
        ])
        result = run_agent_loop(
            router, system_prompt="t", user_prompt="go",
            tools=[_schema("broken"), _schema("fin")],
            tool_handlers={"broken": sometimes_error, "fin": done_tool},
            max_consecutive_errors=1, max_iterations=5,
        )
        # done_signalled → result.done=True before circuit check
        assert result.done is True


class TestCircuitBreakerResultSubtypeExists:
    def test_subtype_constant_exists(self):
        assert hasattr(ResultSubtype, "error_consecutive_tool_failures")
        assert ResultSubtype.error_consecutive_tool_failures == "error_consecutive_tool_failures"
