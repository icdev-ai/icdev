# CUI // SP-CTI
"""Compaction failure and budget blocks are first-class agent-loop outcomes (hgx-ctxw-02).

Both used to surface as ``error_during_execution``:

* a compressor exception was swallowed with a WARNING and the UNCHANGED,
  oversized transcript was handed to the provider, whose rejection is what the
  caller actually saw;
* ``router.invoke()`` raises a budget error BEFORE any model is called, and the
  loop's blanket ``except Exception`` reported that governed refusal as a crash.

Also covers the per-agent budget gate in ``LLMRouter.invoke``, which was
unreachable for loop traffic because the loop never set ``LLMRequest.agent_id``,
and the token/cost hard-stop now being evaluated before a turn's tools are
dispatched rather than only after.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from icdev.tools.llm.agent_loop import (
    AgentLoopCompactionError,
    ResultSubtype,
    _is_budget_block,
    _maybe_compress_messages,
    run_agent_loop,
)
from icdev.tools.llm.provider import LLMRequest, LLMResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeProvider:
    provider_name: str = "anthropic"


class RecordingRouter:
    """Router that records each request and returns a scripted response sequence.

    ``responses`` entries are either a list of tool-call dicts (a tool_use turn)
    or a string (a final-answer turn). An entry that is an ``Exception`` instance
    is raised from :meth:`invoke` instead, which is how the budget-block paths
    are exercised without reaching a provider.
    """

    def __init__(
        self,
        responses: list[Any],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []
        self._provider = _FakeProvider()
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cost_usd = cost_usd

    def get_provider_for_function(self, function: str):
        return self._provider, "fake-model", {"supports_tools": True}

    def invoke(self, function: str, request: LLMRequest) -> LLMResponse:
        idx = len(self.requests)
        self.requests.append(request)
        entry = self._responses[idx]
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, str):
            return LLMResponse(
                content=entry,
                stop_reason="end_turn",
                provider="fake",
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                cost_usd=self._cost_usd,
            )
        return LLMResponse(
            content="",
            tool_calls=list(entry),
            stop_reason="tool_use",
            provider="fake",
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cost_usd=self._cost_usd,
        )


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _boom(*_args, **_kwargs):
    raise RuntimeError("compressor exploded")


# ---------------------------------------------------------------------------
# 1. Compaction failure
# ---------------------------------------------------------------------------


class TestCompactionFailure:
    def test_helper_raises_instead_of_returning_messages_unchanged(self, monkeypatch):
        """A compressor exception must not degrade to 'return the oversized history'."""
        import icdev.tools.llm.context_compressor as _cc

        monkeypatch.setattr(_cc, "compress_messages", _boom)
        messages = [{"role": "user", "content": "x" * 4000}]
        events: list[dict[str, Any]] = []

        with pytest.raises(AgentLoopCompactionError):
            _maybe_compress_messages(
                messages,
                context_window_tokens=10,
                compression_budget_tokens=5,
                compression_events=events,
            )

        assert events, "the failed attempt must still be recorded in compression_events"
        assert events[-1]["method"] == "failed"
        assert "RuntimeError" in events[-1]["error"]

    def test_under_window_never_calls_the_compressor(self, monkeypatch):
        """Compaction is only load-bearing when history is actually over the window."""
        import icdev.tools.llm.context_compressor as _cc

        monkeypatch.setattr(_cc, "compress_messages", _boom)
        messages = [{"role": "user", "content": "short"}]
        assert (
            _maybe_compress_messages(
                messages,
                context_window_tokens=1_000_000,
                compression_budget_tokens=750_000,
                compression_events=[],
            )
            is messages
        )

    def test_forced_compressor_exception_yields_compaction_subtype(self, monkeypatch):
        """ACCEPTANCE: compaction failure is not error_during_execution."""
        import icdev.tools.llm.context_compressor as _cc

        monkeypatch.setattr(_cc, "compress_messages", _boom)
        router = RecordingRouter(["never reached"])

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u" * 4000,
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=3,
            context_window_tokens=10,
        )

        assert result.result_subtype == ResultSubtype.error_context_compaction
        assert result.result_subtype != ResultSubtype.error_during_execution
        assert result.truncation_reason == "context_compaction_failed"
        assert result.truncated is True
        assert result.done is False
        # The whole point: the provider is never handed the oversized transcript.
        assert router.requests == []
        assert any(
            "compaction failed" in m.get("content", "")
            for m in result.messages
            if isinstance(m.get("content"), str)
        )
        assert result.compression_events[-1]["method"] == "failed"


# ---------------------------------------------------------------------------
# 2. Budget blocks
# ---------------------------------------------------------------------------


class TestBudgetBlockOutcome:
    def test_classifier_matches_both_tracker_errors_and_nothing_else(self):
        from tools.agent.token_tracker import BudgetExceededError
        from tools.budget.module_budget_tracker import ModuleBudgetExceededError

        assert _is_budget_block(ModuleBudgetExceededError("generative_intelligence", {}))
        assert _is_budget_block(BudgetExceededError("builder-agent", {}))
        # Subclasses count; unrelated failures do not.
        assert _is_budget_block(type("Sub", (ModuleBudgetExceededError,), {})("m", {}))
        assert not _is_budget_block(RuntimeError("connection reset by peer"))
        assert not _is_budget_block(TimeoutError("read timed out"))

    def test_module_budget_block_yields_budget_subtype(self):
        """ACCEPTANCE: a module-budget block gets a budget-specific subtype."""
        from tools.budget.module_budget_tracker import ModuleBudgetExceededError

        router = RecordingRouter([
            ModuleBudgetExceededError(
                "generative_intelligence",
                {"action": "block", "message": "generative_intelligence budget exhausted"},
            )
        ])

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=3,
        )

        assert result.result_subtype == ResultSubtype.error_budget_blocked
        assert result.result_subtype != ResultSubtype.error_during_execution
        assert result.truncation_reason == "budget_blocked"
        assert result.truncated is True
        assert any(
            "budget gate blocked" in m.get("content", "")
            for m in result.messages
            if isinstance(m.get("content"), str)
        )

    def test_per_agent_budget_block_yields_budget_subtype(self):
        from tools.agent.token_tracker import BudgetExceededError

        router = RecordingRouter([
            BudgetExceededError(
                "builder-agent",
                {"action": "block", "message": "Budget exhausted for builder-agent"},
            )
        ])

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=3,
            agent_id="builder-agent",
        )

        assert result.result_subtype == ResultSubtype.error_budget_blocked
        assert result.truncation_reason == "budget_blocked"

    def test_ordinary_invoke_failure_is_still_error_during_execution(self):
        """Regression guard: only budget refusals get the new subtype."""
        router = RecordingRouter([ConnectionError("connection reset by peer")])

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=3,
        )

        assert result.result_subtype == ResultSubtype.error_during_execution
        assert result.truncation_reason == "error_during_execution"


# ---------------------------------------------------------------------------
# 3. agent_id threading + the router-side per-agent gate
# ---------------------------------------------------------------------------


class TestAgentIdThreading:
    def test_agent_id_present_on_every_request_by_default(self):
        """ACCEPTANCE: agent_id is present on requests the loop issues."""
        router = RecordingRouter([
            [{"id": "c1", "name": "echo", "input": {}}],
            "done",
        ])

        run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=4,
            llm_function="code_generation",
        )

        assert len(router.requests) == 2
        for req in router.requests:
            assert req.agent_id, "the router's per-agent budget gate is behind `if request.agent_id`"
            assert req.agent_id == "agent-loop:code_generation"

    def test_caller_supplied_agent_id_wins(self):
        router = RecordingRouter(["done"])

        run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=2,
            agent_id="builder-agent",
        )

        assert [r.agent_id for r in router.requests] == ["builder-agent"]

    def test_router_per_agent_gate_fires_for_a_request_carrying_an_agent_id(self, monkeypatch):
        """ACCEPTANCE: the per-agent budget gate fires.

        Exercises the real gate in ``LLMRouter.invoke`` (router.py) rather than a
        stand-in: with an ``agent_id`` on the request, a blocking ``check_budget``
        verdict raises before any provider is resolved. This is the path that was
        dead for loop traffic.
        """
        import tools.agent.token_tracker as _tt
        from icdev.tools.llm.router import LLMRouter
        from tools.agent.token_tracker import BudgetExceededError

        monkeypatch.delenv("ICDEV_NO_LLM", raising=False)
        seen: list[str] = []

        def blocking_check_budget(agent_id: str, db_path=None):
            seen.append(agent_id)
            return {
                "action": "block",
                "agent_id": agent_id,
                "message": f"Budget exhausted for {agent_id}",
            }

        monkeypatch.setattr(_tt, "check_budget", blocking_check_budget)

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": "hi"}],
            agent_id="builder-agent",
        )
        with pytest.raises(BudgetExceededError):
            router.invoke("code_generation", request)
        assert seen == ["builder-agent"]

        # And with the field empty — the state the loop used to leave it in —
        # the gate is never consulted at all.
        seen.clear()
        try:
            router.invoke("code_generation", LLMRequest(messages=[{"role": "user", "content": "hi"}]))
        except Exception:
            pass  # any downstream failure is fine; only the gate matters here
        assert seen == []


# ---------------------------------------------------------------------------
# 4. Hard budget evaluated before a turn's tools are dispatched
# ---------------------------------------------------------------------------


class TestHardBudgetBeforeToolDispatch:
    def test_token_ceiling_stops_before_the_tool_sweep(self):
        """A response that blows the ceiling must not still run its tool calls."""
        router = RecordingRouter(
            [
                [{"id": "c1", "name": "echo", "input": {}}],
                "unreachable",
            ],
            input_tokens=600,
            output_tokens=600,
        )
        ran: list[str] = []

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: ran.append("echo") or "x"},
            max_iterations=4,
            max_total_tokens=1000,
        )

        assert result.result_subtype == ResultSubtype.error_max_budget_tokens
        assert result.truncation_reason == "max_total_tokens"
        assert ran == [], "tools ran after the token ceiling was already blown"
        assert result.tool_call_log == []

    def test_cost_ceiling_stops_before_the_tool_sweep(self):
        router = RecordingRouter(
            [
                [{"id": "c1", "name": "echo", "input": {}}],
                "unreachable",
            ],
            cost_usd=5.0,
        )
        ran: list[str] = []

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: ran.append("echo") or "x"},
            max_iterations=4,
            max_cost_usd=1.0,
        )

        assert result.result_subtype == ResultSubtype.error_max_budget_cost
        assert result.truncation_reason == "max_cost_usd"
        assert ran == []

    @staticmethod
    def _blocks(messages: list[dict[str, Any]], kind: str, id_key: str) -> set[str]:
        return {
            block.get(id_key)
            for m in messages
            if isinstance(m.get("content"), list)
            for block in m["content"]
            if isinstance(block, dict) and block.get("type") == kind
        }

    def test_history_never_ends_on_an_unanswered_tool_use(self):
        """The stop must not leave a tool_use block with no matching tool_result.

        Runs the SAME script twice — once under a ceiling that is not reached, so
        the tool_use/tool_result extraction is proven non-vacuous, and once under
        a ceiling the first response already blows.
        """
        def _run(max_total_tokens: int | None):
            return run_agent_loop(
                RecordingRouter(
                    [[{"id": "c1", "name": "echo", "input": {}}], "final"],
                    input_tokens=600,
                    output_tokens=600,
                ),
                system_prompt="s",
                user_prompt="u",
                tools=[_tool("echo")],
                tool_handlers={"echo": lambda i, s: "x"},
                max_iterations=4,
                max_total_tokens=max_total_tokens,
            )

        unbounded = _run(None)
        assert self._blocks(unbounded.messages, "tool_use", "id") == {"c1"}
        assert self._blocks(unbounded.messages, "tool_result", "tool_use_id") == {"c1"}

        stopped = _run(1000)
        assert stopped.result_subtype == ResultSubtype.error_max_budget_tokens
        orphans = self._blocks(stopped.messages, "tool_use", "id") - self._blocks(
            stopped.messages, "tool_result", "tool_use_id"
        )
        assert not orphans, f"orphaned tool_use block(s): {orphans}"

    def test_final_answer_turn_is_not_reclassified_as_a_budget_stop(self):
        """An end_turn that crosses the ceiling still reports success."""
        router = RecordingRouter(["final answer"], input_tokens=600, output_tokens=600)

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("echo")],
            tool_handlers={"echo": lambda i, s: "x"},
            max_iterations=4,
            max_total_tokens=1000,
        )

        assert result.done is True
        assert result.result_subtype == ResultSubtype.success
        assert result.final_content == "final answer"
