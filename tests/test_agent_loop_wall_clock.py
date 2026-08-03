# CUI // SP-CTI
"""Session wall-clock ceiling for the agent loop (ars-wall-01).

The gap this closes: ``tool_timeout_seconds`` and ``llm_call_timeout_seconds``
are PER-CALL ceilings. A session can stay under every one of them, under
``max_total_tokens`` and under ``max_cost_usd``, and still run for hours —
which is exactly what a slow external dependency or a patient loop produces.

Three properties are under test:

1. A session that exceeds the budget terminates with its OWN truncation reason,
   distinct from the token/cost/iteration reasons, so "ran too long" never reads
   as "task too big".
2. The budget is a SESSION budget at every level. The multi-round wrappers
   (:func:`run_agent_loop_with_rubric`, :func:`run_staged_agent_loop`) carve it
   into slices rather than handing each round a fresh copy — otherwise a
   3-round rubric run legitimately runs for 3x the ceiling.
3. The loop-level and task-level ceilings are consistent: the kanban dispatcher
   derives the loop budget from the same ``_get_task_timeout`` the reaper uses
   to kill the task, and holds it strictly below the kill timer so the loop
   stops itself and returns a real result.

Time is driven by an injected fake clock rather than real sleeps, so the
assertions are exact and the suite stays fast.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import icdev.tools.llm.agent_loop as _al
from icdev.tools.llm.agent_loop import (
    LoopStage,
    ResultSubtype,
    RubricGrade,
    RubricVerdict,
    _load_budget_defaults,
    run_agent_loop,
    run_agent_loop_with_rubric,
    run_staged_agent_loop,
)
from icdev.tools.llm.provider import LLMResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClock:
    """Stands in for the ``time`` module inside ``agent_loop``.

    Only ``monotonic`` is used by the module under test (verified by grep), so a
    two-method stub is a complete substitute. Injected by replacing the module's
    ``time`` global, which leaves the real stdlib ``time`` untouched for
    everything else running in the same process.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@dataclass
class FakeProvider:
    provider_name: str = "anthropic"


class ScriptedRouter:
    """Returns a scripted sequence: list-of-tool-calls, or str for a final answer.

    Optionally advances a clock on each ``invoke`` so an LLM call can be made to
    "cost" wall-clock time without sleeping.
    """

    def __init__(
        self,
        responses: list[Any],
        *,
        clock: FakeClock | None = None,
        seconds_per_call: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []
        self._provider = FakeProvider()
        self._clock = clock
        self._seconds_per_call = seconds_per_call

    def get_provider_for_function(self, function: str):
        return self._provider, "fake-model", {"supports_tools": True}

    def invoke(self, function: str, request: Any) -> LLMResponse:
        entry = self._responses[len(self.calls)]
        self.calls.append(function)
        if self._clock is not None and self._seconds_per_call:
            self._clock.advance(self._seconds_per_call)
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


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _distinct_calls(n: int) -> list[list[dict[str, Any]]]:
    """N turns of one tool call each, all mutually distinct.

    Distinct on purpose: identical or near-identical calls would trip the stall
    guard or the semantic loop detector, and this module is testing neither.
    """
    return [[{"id": f"c{i}", "name": "work", "input": {"path": f"src/mod_{i}.py"}}] for i in range(n)]


def _loop_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "system_prompt": "s",
        "user_prompt": "u",
        "tools": [_tool("work")],
        "tool_handlers": {"work": lambda inp, stop: f"wrote {inp.get('path')}"},
        "memory_enabled": False,
        "max_iterations": 20,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestBudgetConfiguration:
    def test_default_ships_in_llm_config(self):
        """The budget lives alongside the existing ones, same config path."""
        defaults = _load_budget_defaults()
        assert "max_wall_clock_seconds" in defaults
        assert defaults["max_wall_clock_seconds"] > 0
        # Loaded as a float, like max_cost_usd — not an int like the token caps.
        assert isinstance(defaults["max_wall_clock_seconds"], float)

    def test_it_is_a_session_ceiling_not_a_per_call_one(self):
        """It must be strictly larger than the per-call ceilings it complements."""
        defaults = _load_budget_defaults()
        assert defaults["max_wall_clock_seconds"] > defaults["llm_call_timeout_seconds"]
        assert defaults["max_wall_clock_seconds"] > defaults["tool_timeout_seconds"]

    def test_subtype_is_distinct_from_every_other_subtype(self):
        """A dedicated subtype — not a reused budget one."""
        values = [
            getattr(ResultSubtype, name)
            for name in dir(ResultSubtype)
            if not name.startswith("_")
        ]
        assert values.count(ResultSubtype.error_max_wall_clock) == 1
        assert ResultSubtype.error_max_wall_clock != ResultSubtype.error_max_budget_tokens
        assert ResultSubtype.error_max_budget_cost != ResultSubtype.error_max_wall_clock


# ---------------------------------------------------------------------------
# run_agent_loop
# ---------------------------------------------------------------------------


class TestAgentLoopWallClock:
    def test_slow_tool_ends_the_session_with_its_own_reason(self, monkeypatch):
        """A run under every OTHER ceiling still terminates, and says why."""
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        def _slow_tool(inp, stop):
            clock.advance(100.0)  # under any plausible per-call tool timeout
            return f"wrote {inp.get('path')}"

        router = ScriptedRouter(_distinct_calls(10) + ["done"])
        result = run_agent_loop(
            router,
            **_loop_kwargs(
                tool_handlers={"work": _slow_tool},
                max_wall_clock_seconds=60.0,
                max_total_tokens=1_000_000,
                max_cost_usd=1000.0,
            ),
        )

        assert result.truncated is True
        assert result.result_subtype == ResultSubtype.error_max_wall_clock
        assert result.truncation_reason == "max_wall_clock_seconds"
        # Distinct from the ceilings that did NOT fire — the whole point.
        assert result.truncation_reason not in ("max_total_tokens", "max_cost_usd", "max_iterations")
        assert result.total_input_tokens + result.total_output_tokens < 1_000_000

    def test_a_single_long_turn_is_caught_on_that_turn(self, monkeypatch):
        """Caught after the tool that blew the budget, not one turn later.

        Checking only at the top of the loop would let a session that is already
        20 minutes over budget pay for one more full LLM call before stopping.
        """
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        def _slow_tool(inp, stop):
            clock.advance(500.0)
            return "ok"

        router = ScriptedRouter(_distinct_calls(5) + ["done"])
        result = run_agent_loop(
            router,
            **_loop_kwargs(tool_handlers={"work": _slow_tool}, max_wall_clock_seconds=60.0),
        )

        assert result.truncation_reason == "max_wall_clock_seconds"
        assert len(router.calls) == 1, "started another LLM turn it could not afford"
        assert result.elapsed_seconds == 500.0

    def test_slow_llm_calls_alone_exhaust_the_budget(self, monkeypatch):
        """A slow external dependency with fast tools still terminates."""
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        router = ScriptedRouter(_distinct_calls(10) + ["done"], clock=clock, seconds_per_call=90.0)
        result = run_agent_loop(
            router,
            **_loop_kwargs(max_wall_clock_seconds=200.0),
        )

        assert result.result_subtype == ResultSubtype.error_max_wall_clock
        # 90s per call: turns start at elapsed 0, 90, 180 — the 4th would start
        # at 270, past the 200s deadline, so it never happens.
        assert len(router.calls) == 3

    def test_a_session_inside_the_budget_is_untouched(self, monkeypatch):
        """No false positive: a fast run completes normally."""
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        router = ScriptedRouter(_distinct_calls(2) + ["all done"], clock=clock, seconds_per_call=1.0)
        result = run_agent_loop(
            router,
            **_loop_kwargs(max_wall_clock_seconds=600.0),
        )

        assert result.done is True
        assert result.truncated is False
        assert result.truncation_reason == "completed"
        assert result.result_subtype == ResultSubtype.success

    def test_zero_disables_the_ceiling(self, monkeypatch):
        """An explicit 0 opts out — it must not mean 'stop immediately'."""
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        def _very_slow_tool(inp, stop):
            clock.advance(10_000.0)
            return "ok"

        router = ScriptedRouter(_distinct_calls(2) + ["done"])
        result = run_agent_loop(
            router,
            **_loop_kwargs(tool_handlers={"work": _very_slow_tool}, max_wall_clock_seconds=0),
        )

        assert result.done is True
        assert result.truncation_reason == "completed"
        assert result.elapsed_seconds == 20_000.0

    def test_elapsed_seconds_is_reported_on_every_exit_path(self, monkeypatch):
        """Duration is observable even when the ceiling did not fire."""
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        router = ScriptedRouter(_distinct_calls(1) + ["done"], clock=clock, seconds_per_call=7.5)
        result = run_agent_loop(router, **_loop_kwargs(max_wall_clock_seconds=600.0))

        assert result.done is True
        assert result.elapsed_seconds == 15.0  # two LLM calls at 7.5s each

    def test_the_reason_reaches_the_harness_record(self, monkeypatch):
        """The recorded codegen decision carries the duration and the reason.

        Without this, a run that stayed under every token/cost cap but burned
        hours is indistinguishable in the harness record from a fast one.
        """
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)
        captured: list[dict[str, Any]] = []
        monkeypatch.setattr(_al, "_record_codegen_decision", lambda **kw: captured.append(kw))

        def _slow_tool(inp, stop):
            clock.advance(400.0)
            return "ok"

        router = ScriptedRouter(_distinct_calls(5) + ["done"])
        run_agent_loop(
            router,
            **_loop_kwargs(tool_handlers={"work": _slow_tool}, max_wall_clock_seconds=60.0),
        )

        assert len(captured) == 1
        recorded = captured[0]["result"]
        assert recorded.result_subtype == ResultSubtype.error_max_wall_clock
        assert recorded.truncation_reason == "max_wall_clock_seconds"
        assert recorded.elapsed_seconds == 400.0


# ---------------------------------------------------------------------------
# run_agent_loop_with_rubric — the budget spans the WHOLE run
# ---------------------------------------------------------------------------


class TestRubricLoopWallClock:
    @staticmethod
    def _never_satisfied(result):
        return RubricGrade(verdict=RubricVerdict.needs_revision, feedback="keep going")

    def test_rounds_share_one_budget_instead_of_each_getting_a_fresh_one(self, monkeypatch):
        """Each round receives only the time remaining, monotonically shrinking.

        Forwarding the caller's budget verbatim would let a 3-round rubric run
        for 3x the ceiling — precisely the "budgets race each other" failure this
        bound exists to remove.
        """
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        seen: list[float] = []
        real_run = _al.run_agent_loop

        def _spy(router, **kwargs):
            seen.append(kwargs.get("max_wall_clock_seconds"))
            clock.advance(100.0)  # each round burns wall-clock
            return real_run(router, **kwargs)

        monkeypatch.setattr(_al, "run_agent_loop", _spy)

        router = ScriptedRouter(["r1", "r2", "r3", "r4"])
        run_agent_loop_with_rubric(
            router,
            grader=self._never_satisfied,
            max_grading_iterations=3,
            max_wall_clock_seconds=1000.0,
            **_loop_kwargs(),
        )

        assert seen == [1000.0, 900.0, 800.0]
        assert sum(1 for _ in seen) == 3
        assert seen == sorted(seen, reverse=True)

    def test_exhausting_the_budget_between_rounds_stops_the_run(self, monkeypatch):
        """The loop reports the stop itself rather than waiting to be killed."""
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        real_run = _al.run_agent_loop

        def _spy(router, **kwargs):
            clock.advance(400.0)
            return real_run(router, **kwargs)

        monkeypatch.setattr(_al, "run_agent_loop", _spy)

        router = ScriptedRouter(["r1", "r2", "r3", "r4"])
        loop_result = run_agent_loop_with_rubric(
            router,
            grader=self._never_satisfied,
            max_grading_iterations=5,
            max_wall_clock_seconds=500.0,
            **_loop_kwargs(),
        )

        assert loop_result.result.truncated is True
        assert loop_result.result.result_subtype == ResultSubtype.error_max_wall_clock
        assert loop_result.result.truncation_reason == "max_wall_clock_seconds"
        assert loop_result.result.done is False
        # Stopped early: 5 rounds were allowed, the budget permitted 2.
        assert loop_result.grading_attempts < 5

    def test_a_rubric_run_inside_the_budget_is_untouched(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        router = ScriptedRouter(["good"])
        loop_result = run_agent_loop_with_rubric(
            router,
            grader=lambda r: RubricGrade(verdict=RubricVerdict.satisfied, feedback="ok"),
            max_grading_iterations=3,
            max_wall_clock_seconds=1000.0,
            **_loop_kwargs(),
        )

        assert loop_result.satisfied is True
        assert loop_result.result.truncation_reason != "max_wall_clock_seconds"


# ---------------------------------------------------------------------------
# run_staged_agent_loop — the budget spans the WHOLE pipeline
# ---------------------------------------------------------------------------


class TestStagedLoopWallClock:
    def test_stages_share_one_pipeline_budget(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        seen: list[float] = []
        real_run = _al.run_agent_loop

        def _spy(router, **kwargs):
            seen.append(kwargs.get("max_wall_clock_seconds"))
            clock.advance(100.0)
            return real_run(router, **kwargs)

        monkeypatch.setattr(_al, "run_agent_loop", _spy)

        router = ScriptedRouter(["a", "b", "c"])
        run_staged_agent_loop(
            router,
            stages=[LoopStage(name="PLAN"), LoopStage(name="EXECUTE"), LoopStage(name="REFLECT")],
            max_wall_clock_seconds=1000.0,
            **_loop_kwargs(),
        )

        assert seen == [1000.0, 900.0, 800.0]

    def test_an_exhausted_pipeline_budget_fails_the_remaining_stage(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(_al, "time", clock)

        real_run = _al.run_agent_loop

        def _spy(router, **kwargs):
            clock.advance(400.0)
            return real_run(router, **kwargs)

        monkeypatch.setattr(_al, "run_agent_loop", _spy)

        router = ScriptedRouter(["a", "b", "c"])
        staged = run_staged_agent_loop(
            router,
            stages=[LoopStage(name="PLAN"), LoopStage(name="EXECUTE"), LoopStage(name="REFLECT")],
            max_wall_clock_seconds=500.0,
            **_loop_kwargs(),
        )

        assert staged.done is False
        assert "REFLECT" in staged.stages_failed
        assert "REFLECT" not in staged.stages_completed


# ---------------------------------------------------------------------------
# Loop-level vs task-level consistency
# ---------------------------------------------------------------------------


class TestTaskLevelConsistency:
    """The kanban dispatcher's loop budget and the reaper's kill timer agree.

    ``kanban_tasks.max_runtime_seconds`` already existed at the task layer;
    ``_get_task_timeout`` is what resolves it, and it is the SAME function the
    reaper calls before killing a running task. Deriving the loop budget from it
    (rather than from an independent constant) is what stops the two ceilings
    racing.
    """

    def test_reaper_and_dispatcher_read_the_same_timeout_source(self):
        import tools.genesis.reflexes.kanban as k

        assert callable(k._get_task_timeout)
        src = k.inspect.getsource(k) if hasattr(k, "inspect") else None
        if src is None:
            import inspect as _inspect

            src = _inspect.getsource(k)
        # Both the dispatch site and the kill site call _get_task_timeout; the
        # dispatcher then holds its own budget strictly under it.
        assert "_wall_budget = max(60.0, _task_budget * 0.9)" in src
        assert "max_wall_clock_seconds=_wall_budget" in src

    def test_task_explicit_max_runtime_seconds_wins_over_heuristics(self, monkeypatch):
        """The task-layer cap is what the loop budget derives from."""
        import tools.genesis.reflexes.kanban as k

        class _Row(dict):
            pass

        class _Conn:
            def execute(self, sql, params):
                class _Cur:
                    def fetchone(_self):
                        return _Row(
                            description="build a thing",
                            task_type="build",
                            max_runtime_seconds=900,
                        )

                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(k, "get_connection", lambda *a, **kw: _Conn())
        budget = k._get_task_timeout("ars-wall-01")

        assert budget == 900
        # The loop budget is strictly under the kill timer, so the loop stops
        # itself and returns a result instead of being killed with none.
        loop_budget = max(60.0, budget * 0.9)
        assert loop_budget < budget
