# CUI // SP-CTI
"""Cancellation of a running agent turn (hgx-ctxw-03).

Before this change ``stop_event`` was read only at the two turn boundaries, and
the loop's ``with ThreadPoolExecutor(...)`` block shut down with ``wait=True`` —
so a cancelled run still blocked until every abandoned tool thread finished. A
"stop" that takes the full ``tool_timeout_seconds`` to land is not a stop.

Four properties are under test:

1. A stop is reported AS a stop — ``truncation_reason == "stop_event"``,
   ``truncated`` False — and never as a truncation, an error, or a
   consecutive-tool-failure, no matter which boundary caught it.
2. The loop stops WAITING promptly: an in-flight handler that is ignoring the
   token does not hold the turn open, and executor teardown does not re-block on
   it. Asserted against wall-clock, well below the tool timeout it would
   otherwise wait out.
3. A cancelled turn still leaves a well-formed transcript: every ``tool_use``
   block the provider emitted is answered by a ``tool_result``, including the
   calls that were abandoned or never started, so the session stays resumable.
4. A queued sequential tool is never STARTED after the stop — the cheapest and
   most valuable boundary, since it is where a mutating tool would have run.

Timing assertions use a generous ceiling (seconds, against a 30s tool timeout)
so they measure the fix rather than the host's scheduler.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest

from icdev.tools.llm.agent_loop import ResultSubtype, run_agent_loop
from icdev.tools.llm.provider import LLMResponse

#: A blocked handler is released within this long — the test's own upper bound,
#: not the loop's. Anything approaching ``TOOL_TIMEOUT`` means the loop waited.
STOP_BUDGET_SECONDS = 10.0
TOOL_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider:
    provider_name: str = "anthropic"


class ScriptedRouter:
    """Returns a scripted sequence: a list of tool calls, or a str final answer.

    ``on_invoke`` fires before each response is produced, which is how a test
    trips the stop event from inside the loop.
    """

    def __init__(self, responses: list[Any], *, on_invoke: Any = None) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []
        self._provider = FakeProvider()
        self._on_invoke = on_invoke

    def get_provider_for_function(self, function: str):
        return self._provider, "fake-model", {"supports_tools": True}

    def invoke(self, function: str, request: Any) -> LLMResponse:
        if self._on_invoke is not None:
            self._on_invoke(len(self.calls))
        entry = self._responses[min(len(self.calls), len(self._responses) - 1)]
        self.calls.append(function)
        if isinstance(entry, str):
            return LLMResponse(
                content=entry,
                stop_reason="end_turn",
                provider="fake",
                input_tokens=10,
                output_tokens=5,
            )
        return LLMResponse(
            content="",
            tool_calls=list(entry),
            stop_reason="tool_use",
            provider="fake",
            input_tokens=10,
            output_tokens=5,
        )


class BlockingRouter(ScriptedRouter):
    """A router whose ``invoke`` blocks until released — a slow provider."""

    def __init__(self, released: threading.Event) -> None:
        super().__init__(["done"])
        self._released = released
        self.entered = threading.Event()

    def invoke(self, function: str, request: Any) -> LLMResponse:
        self.entered.set()
        self._released.wait(timeout=TOOL_TIMEOUT)
        return super().invoke(function, request)


def _tool(name: str, *, read_only: bool = False) -> dict[str, Any]:
    fn: dict[str, Any] = {"name": name, "parameters": {}}
    if read_only:
        fn["is_read_only"] = True
    return {"type": "function", "function": fn}


def _loop_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "system_prompt": "s",
        "user_prompt": "u",
        "tools": [],
        "tool_handlers": {},
        "memory_enabled": False,
        "max_iterations": 8,
        "tool_timeout_seconds": TOOL_TIMEOUT,
        "llm_call_timeout_seconds": TOOL_TIMEOUT,
    }
    base.update(overrides)
    return base


@pytest.fixture
def released():
    """An event every blocked fake waits on; always set, so no thread leaks."""
    ev = threading.Event()
    yield ev
    ev.set()


def _tool_result_ids(messages: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                ids.add(str(block.get("tool_use_id", "")))
    return ids


# ---------------------------------------------------------------------------
# 1. A stop is reported as a stop
# ---------------------------------------------------------------------------


class TestStopIsReportedAsAStop:
    def test_pre_set_token_exits_before_the_first_llm_call(self):
        stop = threading.Event()
        stop.set()
        router = ScriptedRouter(["never reached"])

        result = run_agent_loop(router, stop_event=stop, **_loop_kwargs())

        assert router.calls == []
        assert result.truncation_reason == "stop_event"
        assert result.result_subtype == ResultSubtype.error_stop_event
        assert result.truncated is False
        assert result.done is False

    def test_stop_between_turns_ends_the_run_at_that_boundary(self):
        stop = threading.Event()
        calls: list[str] = []

        def handler(inp: dict[str, Any], _stop: Any) -> str:
            calls.append(str(inp.get("path")))
            stop.set()  # the operator hits Ctrl-C while turn 0's tool runs
            return "ok"

        router = ScriptedRouter(
            [
                [{"id": "c0", "name": "work", "input": {"path": "a.py"}}],
                [{"id": "c1", "name": "work", "input": {"path": "b.py"}}],
            ]
        )
        result = run_agent_loop(
            router,
            stop_event=stop,
            **_loop_kwargs(
                tools=[_tool("work")], tool_handlers={"work": handler}
            ),
        )

        assert calls == ["a.py"]  # turn 1's tool never ran
        assert len(router.calls) == 1  # and turn 1 never asked the provider
        assert result.truncation_reason == "stop_event"
        assert result.truncated is False

    def test_a_stop_that_errored_every_tool_is_not_a_tool_failure(self):
        """The abandoned-tool results must not read as a circuit-breaker trip.

        Every abandoned call is recorded as an error result, so with
        ``max_consecutive_errors=1`` the circuit breaker and the stop check race
        for the same turn. The stop has to win, or "I pressed Ctrl-C" is
        reported to the operator as "your tools are broken".
        """
        stop = threading.Event()

        def handler(_inp: dict[str, Any], _stop: Any) -> str:
            stop.set()
            raise RuntimeError("boom")

        router = ScriptedRouter(
            [[{"id": "c0", "name": "work", "input": {"path": "a.py"}}]]
        )
        result = run_agent_loop(
            router,
            stop_event=stop,
            **_loop_kwargs(
                tools=[_tool("work")],
                tool_handlers={"work": handler},
                max_consecutive_errors=1,
            ),
        )

        assert result.truncation_reason == "stop_event"
        assert result.result_subtype == ResultSubtype.error_stop_event


# ---------------------------------------------------------------------------
# 2. The loop stops waiting promptly
# ---------------------------------------------------------------------------


class TestTheLoopDoesNotWaitOutAbandonedWork:
    def test_an_in_flight_tool_does_not_hold_the_turn_open(self, released):
        """The regression: a handler ignoring the token used to pin the turn.

        The handler blocks for ``TOOL_TIMEOUT``; the token is set from another
        thread a moment later. ``Future.result(timeout=30)`` is one
        uninterruptible sleep, so before the fix this returned in ~30s.
        """
        stop = threading.Event()
        entered = threading.Event()

        def blocking_handler(_inp: dict[str, Any], _stop: Any) -> str:
            entered.set()
            released.wait(timeout=TOOL_TIMEOUT)  # deliberately ignores the token
            return "finally done"

        router = ScriptedRouter(
            [[{"id": "c0", "name": "slow", "input": {}}], "wrapped up"]
        )

        def _stopper() -> None:
            entered.wait(timeout=STOP_BUDGET_SECONDS)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()

        started = time.monotonic()
        result = run_agent_loop(
            router,
            stop_event=stop,
            **_loop_kwargs(
                tools=[_tool("slow", read_only=True)],
                tool_handlers={"slow": blocking_handler},
            ),
        )
        elapsed = time.monotonic() - started

        assert entered.is_set(), "the handler must actually have been running"
        assert elapsed < STOP_BUDGET_SECONDS, (
            f"cancelled run took {elapsed:.1f}s — the loop waited out the "
            f"abandoned tool instead of abandoning it"
        )
        assert result.truncation_reason == "stop_event"

    def test_executor_teardown_does_not_rejoin_abandoned_threads(self, released):
        """`with ThreadPoolExecutor` shuts down with wait=True — the second trap.

        Even with an interruptible wait, leaving the ``with`` block would rejoin
        the abandoned worker. Timed across the WHOLE call, so it covers teardown
        as well as the wait.
        """
        stop = threading.Event()
        entered = threading.Event()

        def blocking_handler(_inp: dict[str, Any], _stop: Any) -> str:
            entered.set()
            released.wait(timeout=TOOL_TIMEOUT)
            return "late"

        router = ScriptedRouter([[{"id": "c0", "name": "slow", "input": {}}]])

        def _stopper() -> None:
            entered.wait(timeout=STOP_BUDGET_SECONDS)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()

        started = time.monotonic()
        run_agent_loop(
            router,
            stop_event=stop,
            **_loop_kwargs(
                tools=[_tool("slow", read_only=True)],
                tool_handlers={"slow": blocking_handler},
            ),
        )
        elapsed = time.monotonic() - started

        assert elapsed < STOP_BUDGET_SECONDS, (
            f"run_agent_loop returned only after {elapsed:.1f}s — executor "
            f"shutdown rejoined the abandoned worker"
        )

    def test_a_stop_during_the_llm_call_lands_before_the_call_returns(
        self, released
    ):
        """The provider call is the longest wait in a turn; it must be cancellable."""
        stop = threading.Event()
        router = BlockingRouter(released)

        def _stopper() -> None:
            router.entered.wait(timeout=STOP_BUDGET_SECONDS)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()

        started = time.monotonic()
        result = run_agent_loop(router, stop_event=stop, **_loop_kwargs(tools=[]))
        elapsed = time.monotonic() - started

        assert router.entered.is_set()
        assert elapsed < STOP_BUDGET_SECONDS
        assert result.truncation_reason == "stop_event"
        assert result.truncated is False


# ---------------------------------------------------------------------------
# 3. A cancelled turn leaves a resumable transcript
# ---------------------------------------------------------------------------


class TestTheTranscriptStaysWellFormed:
    def test_every_tool_use_block_is_answered_even_when_abandoned(self, released):
        """An unanswered ``tool_use`` is a protocol error on resume.

        Two calls in one turn: one abandoned mid-flight, one never started.
        Both must still produce a ``tool_result`` keyed to their id.
        """
        stop = threading.Event()
        entered = threading.Event()

        def blocking_handler(_inp: dict[str, Any], _stop: Any) -> str:
            entered.set()
            released.wait(timeout=TOOL_TIMEOUT)
            return "late"

        def quick_handler(_inp: dict[str, Any], _stop: Any) -> str:
            return "quick"

        router = ScriptedRouter(
            [
                [
                    {"id": "c0", "name": "slow", "input": {}},
                    {"id": "c1", "name": "quick", "input": {}},
                ]
            ]
        )

        def _stopper() -> None:
            entered.wait(timeout=STOP_BUDGET_SECONDS)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()

        result = run_agent_loop(
            router,
            stop_event=stop,
            **_loop_kwargs(
                tools=[_tool("slow", read_only=True), _tool("quick")],
                tool_handlers={"slow": blocking_handler, "quick": quick_handler},
            ),
        )

        assert _tool_result_ids(result.messages) == {"c0", "c1"}
        assert result.truncation_reason == "stop_event"


# ---------------------------------------------------------------------------
# 4. Queued sequential tools are never started after a stop
# ---------------------------------------------------------------------------


class TestQueuedToolsAreNotStarted:
    def test_a_sequential_tool_queued_behind_the_stop_never_runs(self):
        """The boundary that matters most: the next mutating call must not fire."""
        stop = threading.Event()
        ran: list[str] = []

        def first(_inp: dict[str, Any], _stop: Any) -> str:
            ran.append("first")
            stop.set()
            return "ok"

        def second(_inp: dict[str, Any], _stop: Any) -> str:
            ran.append("second")
            return "ok"

        router = ScriptedRouter(
            [
                [
                    {"id": "c0", "name": "first", "input": {}},
                    {"id": "c1", "name": "second", "input": {}},
                ]
            ]
        )
        result = run_agent_loop(
            router,
            stop_event=stop,
            **_loop_kwargs(
                tools=[_tool("first"), _tool("second")],
                tool_handlers={"first": first, "second": second},
            ),
        )

        assert ran == ["first"], "the queued tool ran after the run was stopped"
        # ...and it was still answered, so the transcript stays valid.
        assert _tool_result_ids(result.messages) == {"c0", "c1"}
        assert result.truncation_reason == "stop_event"


# ---------------------------------------------------------------------------
# Absent token — the unchanged path
# ---------------------------------------------------------------------------


def test_no_stop_event_behaves_exactly_as_before():
    """``stop_event=None`` must not pay for any of this."""
    router = ScriptedRouter(
        [[{"id": "c0", "name": "work", "input": {}}], "all done"]
    )
    result = run_agent_loop(
        router,
        **_loop_kwargs(
            tools=[_tool("work")],
            tool_handlers={"work": lambda inp, stop: "ok"},
        ),
    )
    assert result.done is True
    assert result.truncation_reason == "completed"
    assert result.final_content == "all done"
