"""Pluggable code-grader path of ``run_agent_loop_with_rubric``.

These tests exercise the ``grader=`` extension only. They use a FAKE router
whose ``invoke`` must never be called (a code grader routes no LLM), so they
pass under any provider or none — the primitive is LLM-agnostic.
"""

import pytest

from icdev.tools.llm import agent_loop as al
from icdev.tools.llm.agent_loop import (
    AgentLoopResult,
    RubricGrade,
    RubricVerdict,
    run_agent_loop_with_rubric,
)


class FakeRouter:
    """A router whose ``invoke`` must never fire when a code grader is used."""

    def __init__(self):
        self.invocations = []

    def invoke(self, *args, **kwargs):  # pragma: no cover - asserted never called
        self.invocations.append((args, kwargs))
        raise AssertionError("router.invoke called — code grader must not route an LLM")


@pytest.fixture
def fake_loop(monkeypatch):
    """Replace run_agent_loop with a stub returning queued results; record kwargs."""
    calls = []
    queue = []

    def _stub(router, **kwargs):
        calls.append(kwargs)
        return queue.pop(0) if queue else AgentLoopResult(done=True, final_content="")

    monkeypatch.setattr(al, "run_agent_loop", _stub)
    return calls, queue


def _done(content="ok", messages=None):
    return AgentLoopResult(done=True, final_content=content, messages=messages or [])


def test_grader_satisfied_first_round_stops(fake_loop):
    calls, queue = fake_loop
    queue.append(_done("v1"))
    router = FakeRouter()

    res = run_agent_loop_with_rubric(
        router,
        grader=lambda r: RubricGrade(verdict=RubricVerdict.satisfied, feedback="good"),
        system_prompt="s",
        user_prompt="u",
    )

    assert res.satisfied is True
    assert res.grading_attempts == 1
    assert len(calls) == 1
    assert router.invocations == []  # LLM-agnostic: grader routed nothing


def test_needs_revision_then_satisfied_resumes_with_feedback(fake_loop):
    calls, queue = fake_loop
    queue.append(_done("v1", messages=[{"role": "assistant", "content": "v1"}]))
    queue.append(_done("v2"))
    verdicts = iter([
        RubricGrade(verdict=RubricVerdict.needs_revision, feedback="fix the widget"),
        RubricGrade(verdict=RubricVerdict.satisfied, feedback="good"),
    ])
    router = FakeRouter()

    res = run_agent_loop_with_rubric(
        router,
        grader=lambda r: next(verdicts),
        system_prompt="s",
        user_prompt="u",
    )

    assert res.satisfied is True
    assert res.grading_attempts == 2
    assert len(calls) == 2
    # second call resumes from transcript with the grader feedback injected
    resumed = calls[1]["initial_messages"]
    assert any("fix the widget" in str(m.get("content", "")) for m in resumed)
    assert router.invocations == []


def test_grader_raise_degrades_to_grader_error_and_stops(fake_loop):
    calls, queue = fake_loop
    queue.append(_done("v1"))

    def _boom(r):
        raise RuntimeError("gate infra down")

    res = run_agent_loop_with_rubric(
        FakeRouter(), grader=_boom, system_prompt="s", user_prompt="u"
    )

    assert res.satisfied is False
    assert res.grades[-1].verdict == RubricVerdict.grader_error
    assert "gate infra down" in res.grades[-1].feedback
    assert len(calls) == 1  # grader_error is terminal


def test_bad_return_type_degrades_to_grader_error(fake_loop):
    _, queue = fake_loop
    queue.append(_done("v1"))
    res = run_agent_loop_with_rubric(
        FakeRouter(), grader=lambda r: "not-a-grade", system_prompt="s", user_prompt="u"
    )
    assert res.grades[-1].verdict == RubricVerdict.grader_error


def test_unrecognized_verdict_degrades_to_grader_error(fake_loop):
    _, queue = fake_loop
    queue.append(_done("v1"))
    res = run_agent_loop_with_rubric(
        FakeRouter(),
        grader=lambda r: RubricGrade(verdict="meh", feedback="?"),
        system_prompt="s",
        user_prompt="u",
    )
    assert res.grades[-1].verdict == RubricVerdict.grader_error


def test_max_iterations_reached_when_always_needs_revision(fake_loop):
    _, queue = fake_loop
    for _ in range(5):
        queue.append(_done("v"))
    res = run_agent_loop_with_rubric(
        FakeRouter(),
        grader=lambda r: RubricGrade(verdict=RubricVerdict.needs_revision, feedback="more"),
        max_grading_iterations=3,
        system_prompt="s",
        user_prompt="u",
    )
    assert res.satisfied is False
    assert res.grading_attempts == 3
    assert res.grades[-1].verdict == RubricVerdict.max_iterations_reached


def test_agent_loop_not_done_is_not_graded(fake_loop):
    calls, queue = fake_loop
    queue.append(AgentLoopResult(done=False, final_content="truncated"))
    graded = []
    res = run_agent_loop_with_rubric(
        FakeRouter(),
        grader=lambda r: graded.append(1) or RubricGrade(verdict=RubricVerdict.satisfied),
        system_prompt="s",
        user_prompt="u",
    )
    assert res.satisfied is False
    assert graded == []  # never graded a partial/failed loop
    assert len(calls) == 1


def test_rubric_and_grader_both_rejected():
    with pytest.raises(ValueError):
        run_agent_loop_with_rubric(
            FakeRouter(), rubric="x", grader=lambda r: RubricGrade(), user_prompt="u"
        )


def test_neither_rubric_nor_grader_rejected():
    with pytest.raises(ValueError):
        run_agent_loop_with_rubric(FakeRouter(), user_prompt="u")


def test_initial_messages_still_rejected():
    with pytest.raises(ValueError):
        run_agent_loop_with_rubric(
            FakeRouter(),
            grader=lambda r: RubricGrade(verdict=RubricVerdict.satisfied),
            initial_messages=[{"role": "user", "content": "x"}],
        )
