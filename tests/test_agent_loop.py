# CUI // SP-CTI
"""Unit tests for the reusable agent-loop primitive (icdev.tools.llm.agent_loop)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from icdev.tools.llm.agent_loop import (
    DONE,
    AgentLoopResult,
    AgentLoopUnsupported,
    run_agent_loop,
)
from icdev.tools.llm.provider import LLMResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider:
    provider_name: str = "anthropic"


class ScriptedRouter:
    """Router that returns a scripted sequence of LLMResponses.

    Each entry in ``responses`` is either:
      - a list of tool-call dicts  -> response with tool_calls + stop_reason "tool_use"
      - a str                       -> final text response (no tool_calls)
    """

    def __init__(self, responses: list[Any], *, provider_name: str = "anthropic",
                 supports_tools: bool = True) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._provider = FakeProvider(provider_name=provider_name)
        self._model_config = {"supports_tools": supports_tools}

    def get_provider_for_function(self, function: str):
        return self._provider, "fake-model", self._model_config

    def invoke(self, function: str, request: Any) -> LLMResponse:
        idx = len(self.calls)
        entry = self._responses[idx]
        self.calls.append({
            "function": function,
            "messages_len": len(request.messages),
            "tools_count": len(request.tools or []),
        })
        if isinstance(entry, str):
            return LLMResponse(content=entry, stop_reason="end_turn", provider="fake")
        # list of tool-call dicts
        return LLMResponse(
            content="",
            tool_calls=list(entry),
            stop_reason="tool_use",
            provider="fake",
        )


def _tool(name: str, **params) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


# ---------------------------------------------------------------------------
# Termination & message history
# ---------------------------------------------------------------------------


class TestBasicLoop:
    def test_tool_call_then_end_turn(self):
        router = ScriptedRouter([
            [{"id": "c1", "name": "echo", "input": {"x": 1}}],
            "final answer",
        ])
        seen: list[dict[str, Any]] = []

        def echo(inp, stop):
            seen.append(inp)
            return f"echoed {inp['x']}"

        result = run_agent_loop(
            router,
            system_prompt="sys",
            user_prompt="do it",
            tools=[_tool("echo")],
            tool_handlers={"echo": echo},
            llm_function="code_generation",
        )
        assert result.done is True
        assert result.truncated is False
        assert result.turns == 2
        assert result.final_content == "final answer"
        assert seen == [{"x": 1}]
        assert len(result.tool_call_log) == 1
        assert result.tool_call_log[0]["name"] == "echo"
        assert result.tool_call_log[0]["result"] == "echoed 1"

        # Message history: user, assistant(tool_use), user(tool_result), assistant(text)
        roles = [m["role"] for m in result.messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        # assistant tool_use block present
        asst = result.messages[1]["content"]
        assert any(b.get("type") == "tool_use" and b["name"] == "echo" for b in asst)
        # user tool_result block present
        tr = result.messages[2]["content"]
        assert any(b.get("type") == "tool_result" and b["tool_use_id"] == "c1" for b in tr)

    def test_no_tools_in_first_response_terminates_immediately(self):
        router = ScriptedRouter(["immediate answer"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
        )
        assert result.done is True
        assert result.turns == 1
        assert result.final_content == "immediate answer"
        assert result.tool_call_log == []

    def test_done_sentinel_terminates(self):
        router = ScriptedRouter([
            [{"id": "c1", "name": "done", "input": {"summary": "ok"}}],
        ])

        def done_handler(inp, stop):
            return DONE

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("done")], tool_handlers={"done": done_handler},
        )
        assert result.done is True
        assert result.turns == 1
        assert result.tool_call_log[0]["result"] == "DONE"
        # tool_result message appended with the friendly confirmation text
        tr = result.messages[-1]["content"]
        assert any(b.get("type") == "tool_result" and "Task complete" in b["content"][0]["text"] for b in tr)


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


class TestBounds:
    def test_max_iterations_truncates(self):
        # Always returns a tool call → never ends on its own.
        router = ScriptedRouter([
            [{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(100)
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=3,
        )
        assert result.truncated is True
        assert result.done is False
        assert result.turns == 3

    def test_stop_event_aborts(self):
        router = ScriptedRouter([
            [{"id": "c0", "name": "echo", "input": {}}],
            [{"id": "c1", "name": "echo", "input": {}}],
        ])
        stop = threading.Event()

        def echo(inp, s):
            stop.set()  # signal stop after first tool
            return "r"

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": echo},
            max_iterations=10, stop_event=stop,
        )
        assert result.done is False
        assert result.turns == 1  # second turn never runs


# ---------------------------------------------------------------------------
# Error surfacing
# ---------------------------------------------------------------------------


class TestErrorSurfacing:
    def test_handler_exception_becomes_error_tool_result(self):
        router = ScriptedRouter([
            [{"id": "c1", "name": "boom", "input": {}}],
            "recovered",
        ])

        def boom(inp, s):
            raise ValueError("kaboom")

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("boom")], tool_handlers={"boom": boom},
        )
        assert result.done is True
        assert result.tool_call_log[0]["error"] is not None
        assert "kaboom" in result.tool_call_log[0]["error"]
        # is_error tool_result surfaced to the LLM
        tr = result.messages[2]["content"]
        assert any(b.get("is_error") is True and "kaboom" in b["content"][0]["text"] for b in tr)

    def test_unknown_tool_surfaces_error(self):
        router = ScriptedRouter([
            [{"id": "c1", "name": "nope", "input": {}}],
            "done",
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("nope")], tool_handlers={},
        )
        assert result.done is True
        assert "not registered" in result.tool_call_log[0]["error"]


# ---------------------------------------------------------------------------
# Capability guard
# ---------------------------------------------------------------------------


class TestCapabilityGuard:
    def test_cli_provider_rejected(self):
        router = ScriptedRouter(["x"], provider_name="cli")
        with pytest.raises(AgentLoopUnsupported, match="CLI bridge"):
            run_agent_loop(
                router, system_prompt="s", user_prompt="u",
                tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            )

    def test_supports_tools_false_rejected(self):
        router = ScriptedRouter(["x"], supports_tools=False)
        with pytest.raises(AgentLoopUnsupported, match="supports_tools"):
            run_agent_loop(
                router, system_prompt="s", user_prompt="u",
                tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            )

    def test_no_provider_rejected(self):
        class NullRouter(ScriptedRouter):
            def get_provider_for_function(self, function):
                return None, "", {}
        with pytest.raises(AgentLoopUnsupported, match="No available"):
            run_agent_loop(
                NullRouter(["x"]), system_prompt="s", user_prompt="u",
                tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            )


# ---------------------------------------------------------------------------
# on_turn callback
# ---------------------------------------------------------------------------


class TestTurnCallback:
    def test_on_turn_called_each_turn(self):
        router = ScriptedRouter([
            [{"id": "c1", "name": "echo", "input": {}}],
            "final",
        ])
        seen: list[int] = []

        def on_turn(turn, response, messages):
            seen.append(turn)

        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            on_turn=on_turn,
        )
        assert seen == [0, 1]