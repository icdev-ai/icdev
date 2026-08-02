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
    ResultSubtype,
    RubricVerdict,
    run_agent_loop,
    run_agent_loop_with_rubric,
)
from icdev.tools.llm.provider import LLMResponse


@pytest.fixture(autouse=True)
def _no_approval_gate(monkeypatch):
    """Opt this file out of the ars-appr-01 approval gate.

    These tests exercise loop *mechanics* with synthetic tools ("echo", "slow",
    "boom"). The gate correctly classifies those as unknown and halts them for
    approval, which would make every one of them assert on the gate instead of on
    the behaviour it is actually about.

    The gate's own default-on behaviour is proven in
    ``tests/test_approval_gate.py::test_run_agent_loop_gates_an_unknown_tool_by_default``
    — do not add gate assertions here, and do not read this fixture as evidence
    that the gate is opt-in. It is on by default.
    """
    monkeypatch.setenv("ICDEV_AGENT_APPROVAL_GATE", "0")


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


# ---------------------------------------------------------------------------
# ResultSubtype
# ---------------------------------------------------------------------------


class TestResultSubtype:
    def test_success_on_clean_completion(self):
        router = ScriptedRouter(["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
        )
        from icdev.tools.llm.agent_loop import ResultSubtype
        assert result.result_subtype == ResultSubtype.success

    def test_success_on_done_sentinel(self):
        router = ScriptedRouter([[{"id": "c1", "name": "done_tool", "input": {}}]])
        from icdev.tools.llm.agent_loop import ResultSubtype
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("done_tool")],
            tool_handlers={"done_tool": lambda i, s: __import__("icdev.tools.llm.agent_loop", fromlist=["DONE"]).DONE},
        )
        assert result.result_subtype == ResultSubtype.success

    def test_error_max_turns(self):
        from icdev.tools.llm.agent_loop import ResultSubtype
        router = ScriptedRouter([[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(10)])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=2,
        )
        assert result.result_subtype == ResultSubtype.error_max_turns

    def test_error_max_budget_tokens(self):
        from icdev.tools.llm.agent_loop import ResultSubtype
        router = ScriptedRouter(
            [[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(10)],
            input_tokens=1000, output_tokens=1000,
        )
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=10, max_total_tokens=1500,
        )
        assert result.result_subtype == ResultSubtype.error_max_budget_tokens

    def test_error_max_budget_cost(self):
        from icdev.tools.llm.agent_loop import ResultSubtype
        router = ScriptedRouter(
            [[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(10)],
            cost_usd=2.0,
        )
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=10, max_cost_usd=1.5,
        )
        assert result.result_subtype == ResultSubtype.error_max_budget_cost

    def test_error_stop_event(self):
        from icdev.tools.llm.agent_loop import ResultSubtype
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
        assert result.result_subtype == ResultSubtype.error_stop_event


# ---------------------------------------------------------------------------
# Per-tool timeout
# ---------------------------------------------------------------------------


class TestPerToolTimeout:
    def test_hung_sequential_tool_times_out(self):
        """A handler that never returns gets a TimeoutError → error tool_result."""
        import time
        router = ScriptedRouter([
            [{"id": "c1", "name": "slow", "input": {}}],
            "recovered",
        ])

        def slow_handler(inp, stop):
            time.sleep(10)  # far beyond the timeout
            return "never"

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("slow")], tool_handlers={"slow": slow_handler},
            tool_timeout_seconds=0.1,
        )
        assert result.done is True  # loop recovered after the error
        assert result.tool_call_log[0]["error"] is not None
        assert "timed out" in result.tool_call_log[0]["error"]
        # is_error tool_result sent to LLM
        tr = result.messages[2]["content"]
        assert any(b.get("is_error") is True for b in tr)

    def test_fast_tool_passes_under_timeout(self):
        """A tool that returns quickly should not be affected by timeout."""
        router = ScriptedRouter([
            [{"id": "c1", "name": "fast", "input": {}}],
            "done",
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("fast")], tool_handlers={"fast": lambda i, s: "quick"},
            tool_timeout_seconds=5.0,
        )
        assert result.done is True
        assert result.tool_call_log[0]["error"] is None
        assert result.tool_call_log[0]["result"] == "quick"

    def test_timeout_loaded_from_config(self):
        """tool_timeout_seconds defaults are readable from llm_config.yaml."""
        from icdev.tools.llm.agent_loop import _load_budget_defaults
        defaults = _load_budget_defaults()
        assert "tool_timeout_seconds" in defaults
        assert defaults["tool_timeout_seconds"] == 30.0


# ---------------------------------------------------------------------------
# Parallel read-only tool execution
# ---------------------------------------------------------------------------


def _ro_tool(name: str) -> dict[str, Any]:
    """Tool schema with is_read_only=True."""
    return {
        "type": "function",
        "is_read_only": True,
        "function": {"name": name, "is_read_only": True, "parameters": {}},
    }


class TestParallelTools:
    def test_two_read_only_tools_run_concurrently(self):
        """Two read-only tools in one turn should both execute (concurrent or not)."""
        router = ScriptedRouter([
            [
                {"id": "c1", "name": "read_a", "input": {"path": "a.txt"}},
                {"id": "c2", "name": "read_b", "input": {"path": "b.txt"}},
            ],
            "final",
        ])
        called: list[str] = []

        def read_a(inp, stop):
            called.append("a")
            return "content_a"

        def read_b(inp, stop):
            called.append("b")
            return "content_b"

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_ro_tool("read_a"), _ro_tool("read_b")],
            tool_handlers={"read_a": read_a, "read_b": read_b},
        )
        assert result.done is True
        assert set(called) == {"a", "b"}
        assert len(result.tool_call_log) == 2

    def test_tool_result_messages_in_original_order(self):
        """Tool result messages must appear in the original tool_call order."""
        router = ScriptedRouter([
            [
                {"id": "first", "name": "read_a", "input": {}},
                {"id": "second", "name": "read_b", "input": {}},
            ],
            "done",
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_ro_tool("read_a"), _ro_tool("read_b")],
            tool_handlers={
                "read_a": lambda i, s: "a_result",
                "read_b": lambda i, s: "b_result",
            },
        )
        # messages: [user, assistant(tool_use x2), user(result x2 — same msg is separate), ...]
        # Each tool_result is its own user message; check IDs are in order c1, c2
        tool_result_msgs = [m for m in result.messages if m["role"] == "user"
                            and isinstance(m["content"], list)
                            and any(b.get("type") == "tool_result" for b in m["content"])]
        ids = [b["tool_use_id"] for m in tool_result_msgs for b in m["content"] if b.get("type") == "tool_result"]
        assert ids == ["first", "second"]

    def test_mixed_read_only_and_sequential(self):
        """A mix of read-only and sequential tools all produce results."""
        router = ScriptedRouter([
            [
                {"id": "r1", "name": "read_file", "input": {"path": "f.py"}},
                {"id": "w1", "name": "write_file", "input": {"path": "out.py", "content": "x"}},
            ],
            "done",
        ])
        calls: list[str] = []
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_ro_tool("read_file"), _tool("write_file")],
            tool_handlers={
                "read_file": lambda i, s: calls.append("r") or "file contents",
                "write_file": lambda i, s: calls.append("w") or "wrote",
            },
        )
        assert result.done is True
        assert set(calls) == {"r", "w"}
        assert len(result.tool_call_log) == 2


# ---------------------------------------------------------------------------
# Lifecycle hooks: on_pre_tool_use / on_post_tool_use / on_stop
# ---------------------------------------------------------------------------


class TestHooks:
    def test_on_pre_tool_use_can_block(self):
        """Returning a string from on_pre_tool_use blocks execution."""
        router = ScriptedRouter([
            [{"id": "c1", "name": "dangerous", "input": {}}],
            "recovered",
        ])
        executed: list[bool] = []

        def dangerous(inp, stop):
            executed.append(True)
            return "executed"

        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("dangerous")],
            tool_handlers={"dangerous": dangerous},
            on_pre_tool_use=lambda name, inp: "Permission denied." if name == "dangerous" else None,
        )
        assert result.done is True
        assert executed == []  # handler was never called
        assert result.tool_call_log[0]["error"] is not None
        # error tool_result sent to LLM
        tr = result.messages[2]["content"]
        assert any(b.get("is_error") is True for b in tr)

    def test_on_pre_tool_use_allow_returns_none(self):
        """Returning None from on_pre_tool_use allows execution."""
        router = ScriptedRouter([
            [{"id": "c1", "name": "echo", "input": {}}],
            "done",
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "hello"},
            on_pre_tool_use=lambda name, inp: None,
        )
        assert result.done is True
        assert result.tool_call_log[0]["result"] == "hello"

    def test_on_post_tool_use_receives_correct_args(self):
        """on_post_tool_use is called with (name, input, result_text, is_error)."""
        router = ScriptedRouter([
            [{"id": "c1", "name": "echo", "input": {"x": 42}}],
            "done",
        ])
        received: list[tuple] = []

        def post_hook(name, inp, result_text, is_error):
            received.append((name, inp, result_text, is_error))

        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "reply"},
            on_post_tool_use=post_hook,
        )
        assert len(received) == 1
        name, inp, result_text, is_error = received[0]
        assert name == "echo"
        assert inp == {"x": 42}
        assert result_text == "reply"
        assert is_error is False

    def test_on_post_tool_use_is_error_true_on_failure(self):
        """on_post_tool_use receives is_error=True when handler raises."""
        router = ScriptedRouter([
            [{"id": "c1", "name": "boom", "input": {}}],
            "recovered",
        ])
        received: list[bool] = []

        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("boom")],
            tool_handlers={"boom": lambda i, s: (_ for _ in ()).throw(ValueError("oops"))},
            on_post_tool_use=lambda name, inp, r, is_err: received.append(is_err),
        )
        assert received == [True]

    def test_on_stop_fires_on_success(self):
        """on_stop fires when the loop ends cleanly."""
        router = ScriptedRouter(["answer"])
        fired: list[Any] = []

        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            on_stop=lambda r: fired.append(r),
        )
        assert len(fired) == 1
        assert fired[0].done is True

    def test_on_stop_fires_on_truncation(self):
        """on_stop fires even when the loop is truncated."""
        router = ScriptedRouter([[{"id": f"c{i}", "name": "echo", "input": {}}] for i in range(10)])
        fired: list[Any] = []

        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "r"},
            max_iterations=2,
            on_stop=lambda r: fired.append(r),
        )
        assert len(fired) == 1
        assert fired[0].truncated is True


# ---------------------------------------------------------------------------
# Structured output validation
# ---------------------------------------------------------------------------


class TestStructuredOutput:
    def _make_schema(self, required_keys: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "required": required_keys,
            "properties": {k: {"type": "string"} for k in required_keys},
        }

    def test_valid_json_passes_immediately(self):
        """When final_content is valid JSON matching the schema, done stays True."""
        schema = self._make_schema(["name", "value"])
        router = ScriptedRouter(['{"name": "test", "value": "ok"}'])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            output_schema=schema,
        )
        assert result.done is True
        from icdev.tools.llm.agent_loop import ResultSubtype
        assert result.result_subtype == ResultSubtype.success

    def test_invalid_json_retries_and_succeeds(self):
        """First response is invalid JSON; second response is valid — retries succeed."""
        schema = self._make_schema(["status"])
        router = ScriptedRouter([
            "not json at all",
            '{"status": "fixed"}',
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            output_schema=schema,
            max_structured_output_retries=3,
        )
        assert result.done is True
        assert result.turns == 2

    def test_exhausted_retries_sets_error_subtype(self):
        """When all retries are exhausted, result_subtype is error_max_structured_output_retries."""
        schema = self._make_schema(["required_field"])
        router = ScriptedRouter(["bad"] * 10)  # always returns non-JSON
        from icdev.tools.llm.agent_loop import ResultSubtype
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            output_schema=schema,
            max_structured_output_retries=2,
        )
        assert result.done is False
        assert result.truncated is True
        assert result.result_subtype == ResultSubtype.error_max_structured_output_retries

    def test_missing_required_key_triggers_retry(self):
        """JSON that parses but is missing a required key also triggers retry."""
        schema = self._make_schema(["name", "score"])
        router = ScriptedRouter([
            '{"name": "Alice"}',         # missing "score"
            '{"name": "Alice", "score": "A+"}',  # valid
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            output_schema=schema,
        )
        assert result.done is True
        assert result.turns == 2

    def test_no_schema_skips_validation(self):
        """Without output_schema, non-JSON final_content is fine."""
        router = ScriptedRouter(["plain text, not json"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            output_schema=None,
        )
        assert result.done is True
        assert result.final_content == "plain text, not json"


# ---------------------------------------------------------------------------
# Session ID generation
# ---------------------------------------------------------------------------


class TestSessionId:
    def test_session_id_is_uuid_string(self):
        import re
        router = ScriptedRouter(["answer"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
        )
        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        assert uuid_re.match(result.session_id), f"Not a UUID: {result.session_id!r}"

    def test_each_run_gets_distinct_session_id(self):
        r1 = run_agent_loop(ScriptedRouter(["a"]), system_prompt="s", user_prompt="u", tools=[], tool_handlers={})
        r2 = run_agent_loop(ScriptedRouter(["b"]), system_prompt="s", user_prompt="u", tools=[], tool_handlers={})
        assert r1.session_id != r2.session_id

    def test_resume_with_unknown_id_starts_fresh(self, monkeypatch):
        """Non-existent session ID falls back to fresh conversation."""
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.load_session",
            lambda sid: [],
        )
        router = ScriptedRouter(["answer"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="new task",
            tools=[], tool_handlers={},
            resume_session_id="00000000-0000-0000-0000-000000000000",
        )
        assert result.done is True
        assert result.messages[0]["content"] == "new task"

    def test_resume_loads_prior_messages(self, monkeypatch):
        """When load_session returns messages, those become the starting history."""
        prior_messages = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": [{"type": "text", "text": "started"}]},
        ]
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.load_session",
            lambda sid: prior_messages,
        )
        router = ScriptedRouter(["resumed answer"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="ignored",
            tools=[], tool_handlers={},
            resume_session_id="prior-session-id",
        )
        assert result.done is True
        assert result.messages[0]["content"] == "original task"


# ---------------------------------------------------------------------------
# Session persistence (save/load round-trip via in-memory SQLite)
# ---------------------------------------------------------------------------


def _make_session_conn():
    """Return a no-close wrapper around an in-memory SQLite connection.

    The functions under test call conn.close() in a finally block, which would
    destroy the in-memory database before subsequent read calls. The wrapper
    intercepts close() so the connection (and data) survive the whole test.
    """
    import sqlite3

    class _NoClose:
        def __init__(self, c: sqlite3.Connection) -> None:
            self._c = c

        def __getattr__(self, name: str):
            return getattr(self._c, name)

        def close(self) -> None:  # no-op: keep in-memory DB alive
            pass

    real = sqlite3.connect(":memory:")
    real.execute(
        """CREATE TABLE agent_loop_sessions (
            session_id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL DEFAULT '',
            coworker_id TEXT NOT NULL DEFAULT '',
            llm_function TEXT NOT NULL DEFAULT '',
            result_subtype TEXT NOT NULL DEFAULT '',
            turns INTEGER NOT NULL DEFAULT 0,
            total_input_tokens INTEGER NOT NULL DEFAULT 0,
            total_output_tokens INTEGER NOT NULL DEFAULT 0,
            total_cost_usd REAL NOT NULL DEFAULT 0.0,
            messages_json TEXT NOT NULL DEFAULT '[]',
            system_prompt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    real.commit()
    return _NoClose(real)


class TestSessionPersistence:
    def test_save_and_load_round_trip(self, monkeypatch):
        from icdev.tools.llm.agent_loop import AgentLoopResult
        from icdev.tools.llm.agent_loop_session import load_session, save_session

        conn = _make_session_conn()
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.get_canvas_connection", lambda env: conn
        )
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ]
        result = AgentLoopResult(
            done=True, turns=2, session_id="test-session-abc",
            result_subtype=ResultSubtype.success, messages=messages,
        )
        ok = save_session(result, instance_id="inst-1", coworker_id="cw-1")
        assert ok is True
        loaded = load_session("test-session-abc")
        assert loaded == messages

    def test_load_unknown_session_returns_empty(self, monkeypatch):
        from icdev.tools.llm.agent_loop_session import load_session

        conn = _make_session_conn()
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.get_canvas_connection", lambda env: conn
        )
        assert load_session("does-not-exist") == []

    def test_save_without_session_id_returns_false(self):
        from icdev.tools.llm.agent_loop import AgentLoopResult
        from icdev.tools.llm.agent_loop_session import save_session

        result = AgentLoopResult(done=True, session_id="")
        assert save_session(result) is False

    def test_get_session_metadata(self, monkeypatch):
        from icdev.tools.llm.agent_loop import AgentLoopResult
        from icdev.tools.llm.agent_loop_session import get_session_metadata, save_session

        conn = _make_session_conn()
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.get_canvas_connection", lambda env: conn
        )
        result = AgentLoopResult(
            done=True, turns=3, session_id="meta-xyz",
            result_subtype=ResultSubtype.success,
            total_cost_usd=0.05, messages=[{"role": "user", "content": "hi"}],
        )
        save_session(result, coworker_id="cw-meta")
        meta = get_session_metadata("meta-xyz")
        assert meta is not None
        assert meta["session_id"] == "meta-xyz"
        assert meta["turns"] == 3
        assert meta["result_subtype"] == ResultSubtype.success

    def test_upsert_updates_existing_session(self, monkeypatch):
        """Saving the same session_id twice updates in place."""
        from icdev.tools.llm.agent_loop import AgentLoopResult
        from icdev.tools.llm.agent_loop_session import load_session, save_session

        conn = _make_session_conn()
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.get_canvas_connection", lambda env: conn
        )
        r1 = AgentLoopResult(session_id="upsert-id", turns=1, messages=[{"role": "user", "content": "v1"}])
        save_session(r1)
        r2 = AgentLoopResult(session_id="upsert-id", turns=5, messages=[{"role": "user", "content": "v2"}])
        save_session(r2)
        loaded = load_session("upsert-id")
        assert loaded[0]["content"] == "v2"


# ---------------------------------------------------------------------------
# Subagent spawning (spawn_agent tool)
# ---------------------------------------------------------------------------


class TestSpawnAgent:
    def _make_registry(self):
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class FakeSpec:
            coworker_id = "cw-test"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []

        return AgentToolRegistry(FakeSpec(), instance_id="inst-test")

    def test_spawn_agent_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        assert "spawn_agent" in _SCHEMAS

    def test_spawn_agent_handler_resolves(self):
        registry = self._make_registry()
        tools, handlers = registry.build(["spawn_agent"])
        assert "spawn_agent" in handlers
        assert len(tools) == 1

    def test_spawn_agent_runs_child_loop(self, monkeypatch):
        import icdev.tools.ace.agent_tools as _at
        from icdev.tools.llm.agent_loop import AgentLoopResult

        def fake_loop(router, *, max_iterations=6, **kw):
            r = AgentLoopResult(done=True, final_content="child completed", session_id="s1")
            r.result_subtype = ResultSubtype.success
            return r

        class FakeRouter:
            pass

        monkeypatch.setattr(_at, "run_agent_loop", fake_loop)
        monkeypatch.setattr(_at, "LLMRouter", FakeRouter)

        registry = self._make_registry()
        _, handlers = registry.build(["spawn_agent"])
        out = handlers["spawn_agent"]({"task": "summarize readme", "tools": ["done"]}, None)
        assert "child completed" in out
        assert "[sub-agent:" in out

    def test_spawn_agent_empty_task_returns_error(self):
        registry = self._make_registry()
        _, handlers = registry.build(["spawn_agent"])
        out = handlers["spawn_agent"]({"task": ""}, None)
        assert "error" in out.lower()
        assert "task" in out.lower()

    def test_spawn_agent_caps_max_iterations(self, monkeypatch):
        import icdev.tools.ace.agent_tools as _at
        from icdev.tools.llm.agent_loop import AgentLoopResult

        captured: list[int] = []

        def fake_loop(router, *, max_iterations=6, **kw):
            captured.append(max_iterations)
            r = AgentLoopResult(done=True, final_content="ok", session_id="x")
            r.result_subtype = ResultSubtype.success
            return r

        class FakeRouter:
            pass

        monkeypatch.setattr(_at, "run_agent_loop", fake_loop)
        monkeypatch.setattr(_at, "LLMRouter", FakeRouter)

        registry = self._make_registry()
        _, handlers = registry.build(["spawn_agent"])
        handlers["spawn_agent"]({"task": "do it", "max_iterations": 999}, None)
        assert captured == [20]

    def test_spawn_agent_includes_session_id_in_output(self, monkeypatch):
        import icdev.tools.ace.agent_tools as _at
        from icdev.tools.llm.agent_loop import AgentLoopResult

        def fake_loop(router, *, max_iterations=6, **kw):
            r = AgentLoopResult(done=True, final_content="result", session_id="child-session-abc")
            r.result_subtype = ResultSubtype.success
            return r

        class FakeRouter:
            pass

        monkeypatch.setattr(_at, "run_agent_loop", fake_loop)
        monkeypatch.setattr(_at, "LLMRouter", FakeRouter)

        registry = self._make_registry()
        _, handlers = registry.build(["spawn_agent"])
        out = handlers["spawn_agent"]({"task": "go"}, None)
        assert "child-session-abc" in out


# ---------------------------------------------------------------------------
# Loop control mechanisms (Controls 1-5)
# ---------------------------------------------------------------------------


class TestLoopControls:
    """Tests for the 5 loop-control mechanisms added to run_agent_loop."""

    # ------------------------------------------------------------------
    # Control 1: LLM call timeout
    # ------------------------------------------------------------------

    def test_llm_timeout_terminates_loop(self):
        """A router.invoke that exceeds llm_call_timeout_seconds → error_during_execution."""
        import time

        class SlowRouter(ScriptedRouter):
            def invoke(self, fn, req):
                time.sleep(0.15)  # outlasts the 20ms timeout
                return super().invoke(fn, req)

        router = SlowRouter(["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=3,
            llm_call_timeout_seconds=0.02,
        )
        assert result.result_subtype == ResultSubtype.error_during_execution
        assert result.truncated is True

    def test_llm_timeout_none_completes_normally(self):
        """llm_call_timeout_seconds=None → no timeout wrapping, normal completion."""
        router = ScriptedRouter(["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            llm_call_timeout_seconds=None,
        )
        assert result.done is True
        assert result.result_subtype == ResultSubtype.success

    def test_llm_timeout_default_in_config(self):
        """llm_call_timeout_seconds is present in _load_budget_defaults() config."""
        from icdev.tools.llm.agent_loop import _load_budget_defaults
        defaults = _load_budget_defaults()
        assert "llm_call_timeout_seconds" in defaults
        assert defaults["llm_call_timeout_seconds"] > 0

    # ------------------------------------------------------------------
    # Control 2: Budget pressure injection
    # ------------------------------------------------------------------

    def test_budget_pressure_injected_near_end(self):
        """A 'turn(s) remaining' message is injected in the last 20% of max_iterations."""
        class TrackingRouter(ScriptedRouter):
            def __init__(self, responses):
                super().__init__(responses)
                self.captured: list[list[dict]] = []

            def invoke(self, fn, req):
                self.captured.append(list(req.messages))
                return super().invoke(fn, req)

        # 8 distinct tool calls (different paths → novel each time → no stall)
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": f"f{i}.py"}}]
            for i in range(8)
        ]
        router = TrackingRouter(calls + ["done"])
        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "ok"},
            max_iterations=10,
            stall_threshold=100,
        )
        # Pressure fires at turn 8 (turns_remaining=2, pressure_threshold=2).
        all_msgs = [m for batch in router.captured for m in batch]
        pressure = [
            m for m in all_msgs
            if isinstance(m.get("content"), str) and "turn(s) remaining" in m["content"]
        ]
        assert pressure, "budget pressure message was never injected into messages"

    def test_budget_pressure_injected_only_once(self):
        """Budget pressure message is added exactly once even across multiple turns."""
        pressure_count = [0]

        class CountingRouter(ScriptedRouter):
            def invoke(self, fn, req):
                for m in req.messages:
                    if isinstance(m.get("content"), str) and "turn(s) remaining" in m["content"]:
                        pressure_count[0] += 1
                return super().invoke(fn, req)

        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": f"g{i}.py"}}]
            for i in range(8)
        ]
        router = CountingRouter(calls + ["done"])
        run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "ok"},
            max_iterations=10,
            stall_threshold=100,
        )
        # Pressure text may accumulate in successive messages; should appear ≤ 2 times total.
        assert pressure_count[0] <= 2, (
            f"pressure message appeared {pressure_count[0]} times — should be ≤ 2"
        )

    # ------------------------------------------------------------------
    # Control 3: Duplicate call detector
    # ------------------------------------------------------------------

    def test_duplicate_warn_on_third_repeat(self):
        """Third identical (tool, input) call prepends a [Loop guard] Warning."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": "same.py"}}]
            for i in range(3)
        ]
        router = ScriptedRouter(calls + ["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "content"},
            max_iterations=10,
            stall_threshold=100,  # isolate from stall guard
        )
        read_log = [e for e in result.tool_call_log if e["name"] == "read"]
        assert len(read_log) == 3
        third = read_log[2]["result"]
        assert "[Loop guard]" in third
        assert "Warning" in third

    def test_duplicate_blocked_on_fifth_repeat(self):
        """Fifth identical call is blocked: is_error=True, error field has duplicate-blocked marker."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": "dup.py"}}]
            for i in range(5)
        ]
        router = ScriptedRouter(calls + ["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "content"},
            max_iterations=10,
            stall_threshold=100,
            max_consecutive_errors=10,  # isolate from consecutive-error guard
        )
        read_log = [e for e in result.tool_call_log if e["name"] == "read"]
        assert len(read_log) == 5
        fifth = read_log[4]
        assert fifth["error"] is not None
        assert "duplicate-blocked" in fifth["error"]

    def test_different_inputs_not_flagged_as_duplicate(self):
        """Same tool, different inputs each turn → no Loop guard warnings."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": f"unique{i}.py"}}]
            for i in range(5)
        ]
        router = ScriptedRouter(calls + ["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "ok"},
            max_iterations=10,
            stall_threshold=100,
        )
        for entry in result.tool_call_log:
            assert "[Loop guard]" not in entry.get("result", ""), (
                f"False-positive Loop guard on entry: {entry}"
            )
        assert result.done is True

    # ------------------------------------------------------------------
    # Control 4: Compression notification
    # ------------------------------------------------------------------

    def test_compression_notification_appended(self, monkeypatch):
        """After _maybe_compress_messages truncates history, a [System] notice is added."""
        import icdev.tools.llm.agent_loop as _al

        call_n = [0]

        def fake_compress(msgs, **kw):
            call_n[0] += 1
            # Fire on the second call (turn 1), when messages has grown past the
            # initial single-item state, so truncation actually reduces length.
            if call_n[0] == 2 and len(msgs) > 1:
                return msgs[:1]  # simulate compression: keep only first message
            return msgs

        monkeypatch.setattr(_al, "_maybe_compress_messages", fake_compress)
        # Turn 0: one tool call builds up messages; turn 1: fake compression fires.
        router = ScriptedRouter([
            [{"id": "c1", "name": "echo", "input": {}}],
            "done",
        ])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=5,
        )
        notice = [
            m for m in result.messages
            if isinstance(m.get("content"), str) and "compressed" in m["content"].lower()
        ]
        assert notice, "compression notification was not appended after history was compressed"

    # ------------------------------------------------------------------
    # Control 5: Stall detector
    # ------------------------------------------------------------------

    def test_stall_terminates_after_threshold_turns(self):
        """No novel tool call for stall_threshold turns → error_stalled."""
        # Provide many identical responses; stall fires after turn 3 (0-indexed).
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": "stuck.py"}}]
            for i in range(15)
        ]
        router = ScriptedRouter(calls)
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "ok"},
            max_iterations=20,
            stall_threshold=3,
        )
        assert result.result_subtype == ResultSubtype.error_stalled
        assert result.truncated is True
        # Novel at turn 0 (last_progress=0); stall fires at turn 3 (3-0 ≥ 3). turns ≤ 3.
        assert result.turns <= 3

    def test_stall_not_triggered_with_novel_calls(self):
        """Each turn calls a different path → no stall, loop completes successfully."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": f"novel{i}.py"}}]
            for i in range(8)
        ]
        router = ScriptedRouter(calls + ["done"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "ok"},
            max_iterations=15,
            stall_threshold=3,
        )
        assert result.done is True
        assert result.result_subtype == ResultSubtype.success

    def test_stall_threshold_configurable(self):
        """Lower stall_threshold fires sooner; result.turns reflects early termination."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": "x.py"}}]
            for i in range(20)
        ]
        router = ScriptedRouter(calls)
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("read")], tool_handlers={"read": lambda i, s: "ok"},
            max_iterations=20,
            stall_threshold=2,
        )
        assert result.result_subtype == ResultSubtype.error_stalled
        # Novel at turn 0 (last_progress=0); stall fires at turn 2 (2-0 ≥ 2). turns ≤ 2.
        assert result.turns <= 2

    def test_stall_not_triggered_without_any_tool_calls(self):
        """Loop that never calls tools ends normally — stall detector stays dormant."""
        router = ScriptedRouter(["immediate answer"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=5,
            stall_threshold=3,
        )
        assert result.result_subtype != ResultSubtype.error_stalled
        assert result.done is True

    def test_error_stalled_constant_defined(self):
        """ResultSubtype.error_stalled is defined and equals the literal 'error_stalled'."""
        assert ResultSubtype.error_stalled == "error_stalled"

    def test_stall_threshold_default_in_config(self):
        """stall_threshold default is present in _load_budget_defaults() config."""
        from icdev.tools.llm.agent_loop import _load_budget_defaults
        defaults = _load_budget_defaults()
        assert "stall_threshold" in defaults
        assert defaults["stall_threshold"] > 0


# ---------------------------------------------------------------------------
# initial_messages — explicit message-history seeding
# ---------------------------------------------------------------------------


class TestInitialMessages:
    def test_initial_messages_overrides_user_prompt(self):
        router = ScriptedRouter(["hi"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="ignored",
            tools=[], tool_handlers={},
            initial_messages=[{"role": "user", "content": "real prompt"}],
        )
        assert router.calls[0]["messages_len"] == 1
        assert result.messages[0]["content"] == "real prompt"
        assert result.done is True

    def test_initial_messages_takes_precedence_over_resume_session_id(self):
        router = ScriptedRouter(["hi"])
        result = run_agent_loop(
            router, system_prompt="s", user_prompt="ignored",
            tools=[], tool_handlers={},
            resume_session_id="nonexistent-session",
            initial_messages=[{"role": "user", "content": "explicit"}],
        )
        assert result.messages[0]["content"] == "explicit"


# ---------------------------------------------------------------------------
# run_agent_loop_with_rubric — self-graded iteration
# ---------------------------------------------------------------------------


class TestRubricLoop:
    def test_satisfied_on_first_attempt(self):
        router = ScriptedRouter([
            "draft response",
            '{"verdict": "satisfied", "feedback": "meets all criteria"}',
        ])
        result = run_agent_loop_with_rubric(
            router, rubric="Must be polite and under 100 words.",
            system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
        )
        assert result.satisfied is True
        assert result.grading_attempts == 1
        assert len(result.grades) == 1
        assert result.grades[0].verdict == RubricVerdict.satisfied
        assert result.result.final_content == "draft response"

    def test_needs_revision_then_satisfied(self):
        router = ScriptedRouter([
            "draft v1",
            '{"verdict": "needs_revision", "feedback": "add unit tests"}',
            "draft v2",
            '{"verdict": "satisfied", "feedback": "good now"}',
        ])
        result = run_agent_loop_with_rubric(
            router, rubric="Must include tests.",
            system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
            max_grading_iterations=3,
        )
        assert result.satisfied is True
        assert result.grading_attempts == 2
        assert [g.verdict for g in result.grades] == [
            RubricVerdict.needs_revision,
            RubricVerdict.satisfied,
        ]
        assert result.result.final_content == "draft v2"
        # Second agent-loop call resumed with feedback appended: prior 2 messages
        # (user, assistant) + 1 injected feedback message = 3.
        assert router.calls[2]["messages_len"] == 3

    def test_max_grading_iterations_reached(self):
        router = ScriptedRouter([
            "v1",
            '{"verdict": "needs_revision", "feedback": "still missing X"}',
            "v2",
            '{"verdict": "needs_revision", "feedback": "still missing X"}',
        ])
        result = run_agent_loop_with_rubric(
            router, rubric="Must include X.",
            system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
            max_grading_iterations=2,
        )
        assert result.satisfied is False
        assert result.grading_attempts == 2
        assert result.grades[-1].verdict == RubricVerdict.max_iterations_reached
        assert result.result.final_content == "v2"

    def test_failed_verdict_stops_immediately(self):
        router = ScriptedRouter([
            "draft",
            '{"verdict": "failed", "feedback": "rubric is self-contradictory"}',
        ])
        result = run_agent_loop_with_rubric(
            router, rubric="Impossible rubric.",
            system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
            max_grading_iterations=5,
        )
        assert result.satisfied is False
        assert result.grading_attempts == 1
        assert result.grades[0].verdict == RubricVerdict.failed

    def test_grader_error_on_malformed_json_stops_loop(self):
        router = ScriptedRouter([
            "draft",
            "this is not valid JSON",
        ])
        result = run_agent_loop_with_rubric(
            router, rubric="Anything.",
            system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
            max_grading_iterations=5,
        )
        assert result.satisfied is False
        assert result.grading_attempts == 1
        assert result.grades[0].verdict == RubricVerdict.grader_error

    def test_ungraded_when_agent_loop_does_not_complete(self):
        """A truncated/undone agent loop is never handed to the grader."""
        router = ScriptedRouter([
            [{"id": "c1", "name": "echo", "input": {}}],
        ])
        result = run_agent_loop_with_rubric(
            router, rubric="Anything.",
            system_prompt="s", user_prompt="u",
            tools=[_tool("echo")], tool_handlers={"echo": lambda i, s: "ok"},
            max_iterations=1,
        )
        assert result.result.done is False
        assert result.grading_attempts == 0
        assert result.grades == []
        assert result.satisfied is False

    def test_rejects_explicit_initial_messages_kwarg(self):
        router = ScriptedRouter(["draft"])
        with pytest.raises(ValueError):
            run_agent_loop_with_rubric(
                router, rubric="x", system_prompt="s", user_prompt="u",
                tools=[], tool_handlers={}, initial_messages=[],
            )

    def test_grader_llm_function_override(self):
        router = ScriptedRouter([
            "draft",
            '{"verdict": "satisfied", "feedback": "ok"}',
        ])
        run_agent_loop_with_rubric(
            router, rubric="x", system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            llm_function="code_generation", grader_llm_function="quick_grade",
        )
        assert router.calls[0]["function"] == "code_generation"
        assert router.calls[1]["function"] == "quick_grade"

    def test_on_grade_callback_fires_per_round(self):
        router = ScriptedRouter([
            "v1",
            '{"verdict": "needs_revision", "feedback": "fix it"}',
            "v2",
            '{"verdict": "satisfied", "feedback": "ok"}',
        ])
        seen: list[tuple[int, str]] = []
        run_agent_loop_with_rubric(
            router, rubric="x", system_prompt="s", user_prompt="u",
            tools=[], tool_handlers={},
            on_grade=lambda attempt, grade: seen.append((attempt, grade.verdict)),
        )
        assert seen == [
            (1, RubricVerdict.needs_revision),
            (2, RubricVerdict.satisfied),
        ]

    def test_rubric_defaults_present_in_config(self):
        from icdev.tools.llm.agent_loop import _load_budget_defaults
        defaults = _load_budget_defaults()
        assert defaults.get("max_grading_iterations") == 3
        assert defaults.get("grader_max_tokens") == 1024
