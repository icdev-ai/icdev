# CUI // SP-CTI
"""Unit tests for the reusable agent-loop primitive (icdev.tools.llm.agent_loop)."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import pytest

from icdev.tools.llm.agent_loop import (
    DONE,
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

    def __init__(
        self,
        responses: list[Any],
        *,
        provider_name: str = "anthropic",
        supports_tools: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._provider = FakeProvider(provider_name=provider_name)
        self._model_config = {"supports_tools": supports_tools}
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cost_usd = cost_usd

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
            return LLMResponse(
                content=entry,
                stop_reason="end_turn",
                provider="fake",
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                cost_usd=self._cost_usd,
            )
        # list of tool-call dicts
        return LLMResponse(
            content="",
            tool_calls=list(entry),
            stop_reason="tool_use",
            provider="fake",
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cost_usd=self._cost_usd,
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


# ---------------------------------------------------------------------------
# Budget / context-window guardrails
# ---------------------------------------------------------------------------


class TestBudgets:
    def test_max_total_tokens_truncates(self):
        router = ScriptedRouter(
            [[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(10)],
            input_tokens=1000,
            output_tokens=1000,
        )
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=10,
            max_total_tokens=2500,
        )
        assert result.truncated is True
        assert result.truncation_reason == "max_total_tokens"
        assert result.done is False
        # Each turn adds input+output=2000 tokens. After turn 1 total=2000 (OK);
        # after turn 2 total=4000 > 2500, so it stops at the second turn.
        assert result.turns == 2
        assert result.total_input_tokens == 2000
        assert result.total_output_tokens == 2000
        assert "max_total_tokens=2500" in result.messages[-1]["content"]

    def test_max_cost_usd_truncates(self):
        router = ScriptedRouter(
            [[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(10)],
            cost_usd=1.0,
        )
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=10,
            max_cost_usd=2.5,
        )
        assert result.truncated is True
        assert result.truncation_reason == "max_cost_usd"
        assert result.turns == 3
        assert result.total_cost_usd == 3.0
        assert "max_cost_usd=2.5000" in result.messages[-1]["content"]

    def test_context_compression_triggered(self, monkeypatch):
        long_prompt = "word " * 200  # 1000 chars ~ 250 tokens, exceeds window of 40
        router = ScriptedRouter([[{"id": "c1", "name": "echo", "input": {}}], "done"])

        calls: list[tuple[Any, dict[str, Any]]] = []

        @dataclass
        class FakeCompressed:
            messages: list[dict[str, Any]]
            original_tokens: int
            compressed_tokens: int
            compression_ratio: float
            method: str

        def fake_compress(messages, *, budget_tokens, content_type):
            calls.append((messages, {"budget_tokens": budget_tokens, "content_type": content_type}))
            return FakeCompressed(
                messages=messages,
                original_tokens=250,
                compressed_tokens=30,
                compression_ratio=0.12,
                method="fake_compress",
            )

        monkeypatch.setattr(
            "icdev.tools.llm.context_compressor.compress_messages",
            fake_compress,
        )

        result = run_agent_loop(
            router, system_prompt="s", user_prompt=long_prompt,
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=5,
            context_window_tokens=40,
            compression_budget_tokens=30,
        )
        assert result.done is True
        # Compression runs before each LLM turn while messages exceed the window.
        assert len(calls) == 2
        assert all(c[1]["budget_tokens"] == 30 for c in calls)
        assert result.compression_events == [
            {
                "method": "fake_compress",
                "original_tokens": 250,
                "compressed_tokens": 30,
                "compression_ratio": 0.12,
            },
            {
                "method": "fake_compress",
                "original_tokens": 250,
                "compressed_tokens": 30,
                "compression_ratio": 0.12,
            },
        ]

    def test_load_budget_defaults_from_config(self):
        from icdev.tools.llm.agent_loop import _load_budget_defaults

        defaults = _load_budget_defaults()
        assert defaults["max_total_tokens"] == 128000
        assert defaults["context_window_tokens"] == 64000
        assert defaults["compression_budget_tokens"] == 48000
        assert defaults["max_cost_usd"] == 5.00


# ---------------------------------------------------------------------------
# Truncation reason normalization
# ---------------------------------------------------------------------------


class TestTruncationReason:
    def test_completed_reason(self):
        router = ScriptedRouter(["immediate answer"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
        )
        assert result.done is True
        assert result.truncated is False
        assert result.truncation_reason == "completed"

    def test_max_iterations_reason(self):
        router = ScriptedRouter(
            [[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(100)],
        )
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=2,
        )
        assert result.truncation_reason == "max_iterations"

    def test_stop_event_reason(self):
        router = ScriptedRouter([[{"id": "c0", "name": "echo", "input": {}}], "done"])
        stop = threading.Event()

        def echo(inp, s):
            stop.set()
            return "r"

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": echo},
            max_iterations=10, stop_event=stop,
        )
        assert result.truncation_reason == "stop_event"