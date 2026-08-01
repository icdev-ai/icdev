# CUI // SP-CTI
"""Continuous-Harness decision recording from the agent loop (hcx-rt-03).

The Continuous Harness ``harness_eval`` table was starved: the kanban reflex
called ``record_outcome`` on task status transitions, but no code-generation
run ever recorded a *decision* row, so the outcome UPDATE silently no-oped and
the precision/ECE gates computed over nothing. These tests pin the fix: every
agent-loop run records exactly one ``reflex="codegen"`` decision at completion,
so a later ``record_outcome`` for the same task id attaches to a real row.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any

import pytest

from icdev.tools.llm.agent_loop import (
    RubricGrade,
    RubricVerdict,
    run_agent_loop,
    run_agent_loop_with_rubric,
)
from icdev.tools.llm.provider import LLMResponse


class _FakeProvider:
    provider_name = "anthropic"


class ScriptedRouter:
    """Router returning a scripted sequence of LLMResponses.

    Each entry is either a list of tool-call dicts (→ tool_use response) or a
    str (→ final end_turn response). Mirrors the fake in tests/test_agent_loop.py.
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._provider = _FakeProvider()
        self.calls = 0

    def get_provider_for_function(self, function: str):
        return self._provider, "fake-model", {"supports_tools": True}

    def invoke(self, function: str, request: Any) -> LLMResponse:
        entry = self._responses[self.calls]
        self.calls += 1
        if isinstance(entry, str):
            return LLMResponse(content=entry, stop_reason="end_turn", provider="fake")
        return LLMResponse(
            content="", tool_calls=list(entry), stop_reason="tool_use", provider="fake"
        )


@pytest.fixture
def harness_db(icdev_db, monkeypatch):
    """Point storage.get_connection at a temp DB that has harness_eval.

    ``icdev_db`` (conftest) pre-creates MINIMAL_ICDEV_SCHEMA, which now includes
    harness_eval. get_connection re-reads ICDEV_DB_PATH at call time in the
    SQLite branch, so setting it here routes record_decision/record_outcome and
    our read-back queries at the same file.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    return icdev_db


def _fetch_rows(reflex: str = "codegen") -> list:
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return conn.execute(
            "SELECT id, task_id, reflex, decision, confidence, actual_outcome "
            "FROM harness_eval WHERE reflex = %s ORDER BY created_at",
            (reflex,),
        ).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def test_agent_loop_records_codegen_decision(harness_db):
    """A completed run_agent_loop lands one codegen decision keyed on the task id."""
    router = ScriptedRouter(["final answer"])

    result = run_agent_loop(
        router,
        system_prompt="sys",
        user_prompt="do it",
        tools=[],
        tool_handlers={},
        llm_function="code_generation",
        memory_enabled=False,
        harness_task_id="test-task",
    )
    assert result.done is True

    rows = _fetch_rows()
    matching = [r for r in rows if r["task_id"] == "test-task"]
    assert len(matching) == 1, f"expected 1 codegen decision, got {len(matching)}"
    row = matching[0]
    assert row["reflex"] == "codegen"
    assert row["decision"] == "done"
    assert row["confidence"] == pytest.approx(0.7)
    # Not yet resolved — outcome attaches later on the kanban status transition.
    assert row["actual_outcome"] is None


def test_record_outcome_attaches_to_codegen_decision(harness_db):
    """record_outcome for the same task id fills in the decision's actual_outcome."""
    from tools.genesis.harness.eval_harness import record_outcome

    router = ScriptedRouter(["done"])
    run_agent_loop(
        router,
        system_prompt="sys",
        user_prompt="do it",
        tools=[],
        tool_handlers={},
        llm_function="code_generation",
        memory_enabled=False,
        harness_task_id="test-task",
    )

    # Before: NULL outcome.
    row = [r for r in _fetch_rows() if r["task_id"] == "test-task"][0]
    assert row["actual_outcome"] is None

    record_outcome("test-task", "resolved")

    row = [r for r in _fetch_rows() if r["task_id"] == "test-task"][0]
    assert row["actual_outcome"] == "resolved"


def test_no_harness_task_id_records_under_session_id(harness_db):
    """Without an explicit task id a row still lands, keyed on the session id."""
    router = ScriptedRouter(["ok"])
    result = run_agent_loop(
        router,
        system_prompt="sys",
        user_prompt="do it",
        tools=[],
        tool_handlers={},
        llm_function="code_generation",
        memory_enabled=False,
    )
    rows = _fetch_rows()
    assert any(r["task_id"] == result.session_id for r in rows)


def test_rubric_loop_records_exactly_one_decision(harness_db):
    """The rubric variant records ONE decision reflecting the final grade.

    The inner run_agent_loop calls have their own recording suppressed, so even
    across grading rounds exactly one codegen row lands for the task.
    """
    router = ScriptedRouter(["final answer"])

    def _grader(result):
        return RubricGrade(verdict=RubricVerdict.satisfied, feedback="ok")

    loop_result = run_agent_loop_with_rubric(
        router,
        grader=_grader,
        system_prompt="sys",
        user_prompt="build it",
        tools=[],
        tool_handlers={},
        llm_function="code_generation",
        memory_enabled=False,
        harness_task_id="rubric-task",
    )
    assert loop_result.satisfied is True

    rows = [r for r in _fetch_rows() if r["task_id"] == "rubric-task"]
    assert len(rows) == 1, f"expected exactly 1 codegen decision, got {len(rows)}"
    row = rows[0]
    assert row["decision"] == "satisfied"
    assert row["confidence"] == pytest.approx(0.9)  # satisfied -> high


# ── claude-cli executor decision recording (hcx-rt-08) ──────────────────────
#
# The claude-cli executor is the PRIMARY build path but records no decision at
# dispatch — it spawns a subprocess and returns. Its completion is detected in
# the kanban reflex's _check_completed() poll loop, which is where the codegen
# decision must now be recorded so a later record_outcome() has a row to attach
# to. These tests drive _check_completed() with a REAL, already-exited Popen in
# _running (isinstance(proc, subprocess.Popen) is the guard that distinguishes
# it from the _LLMTaskHandle paths, which self-record). A sentinel patched over
# _detect_token_exhaustion halts the heavy verification/PR machinery immediately
# AFTER the decision has been recorded, keeping the test to the recording only.


class _StopAfterRecord(Exception):
    """Sentinel: raised from the patched token check to halt _check_completed
    right after the codegen decision has been recorded."""


def _finished_proc(returncode: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", f"import sys; sys.exit({returncode})"]
    )
    proc.wait(timeout=30)
    assert proc.poll() == returncode
    return proc


def _drive_check_completed(monkeypatch, task_id: str, returncode: int) -> None:
    kmod = importlib.import_module("tools.genesis.reflexes.kanban")

    def _boom(*_a, **_k):
        raise _StopAfterRecord

    monkeypatch.setattr(kmod, "_running", {task_id: _finished_proc(returncode)})
    monkeypatch.setattr(kmod, "_dispatch_times", {})
    monkeypatch.setattr(kmod, "_worktrees", {})
    # Halt everything downstream of the recording (token check is the first
    # call after the record_decision block).
    monkeypatch.setattr(kmod, "_detect_token_exhaustion", _boom)

    with pytest.raises(_StopAfterRecord):
        kmod._check_completed()


def test_claude_cli_completion_records_done_decision(harness_db, monkeypatch):
    """A cleanly-exiting (rc=0) claude-cli subprocess lands one codegen 'done' row."""
    _drive_check_completed(monkeypatch, "cli-done-task", 0)

    rows = [r for r in _fetch_rows() if r["task_id"] == "cli-done-task"]
    assert len(rows) == 1, f"expected 1 codegen decision, got {len(rows)}"
    row = rows[0]
    assert row["reflex"] == "codegen"
    assert row["decision"] == "done"
    assert row["confidence"] == pytest.approx(0.6)
    assert row["actual_outcome"] is None  # attaches later on status transition


def test_claude_cli_nonzero_exit_records_error_decision(harness_db, monkeypatch):
    """A non-zero exit records an error decision at the lower confidence."""
    _drive_check_completed(monkeypatch, "cli-fail-task", 3)

    rows = [r for r in _fetch_rows() if r["task_id"] == "cli-fail-task"]
    assert len(rows) == 1, f"expected 1 codegen decision, got {len(rows)}"
    row = rows[0]
    assert row["decision"] == "error_nonzero_exit"
    assert row["confidence"] == pytest.approx(0.3)


def test_record_outcome_attaches_to_claude_cli_decision(harness_db, monkeypatch):
    """record_outcome for the claude-cli task fills the decision it now has."""
    from tools.genesis.harness.eval_harness import record_outcome

    _drive_check_completed(monkeypatch, "cli-outcome-task", 0)

    row = [r for r in _fetch_rows() if r["task_id"] == "cli-outcome-task"][0]
    assert row["actual_outcome"] is None

    record_outcome("cli-outcome-task", "resolved")

    row = [r for r in _fetch_rows() if r["task_id"] == "cli-outcome-task"][0]
    assert row["actual_outcome"] == "resolved"
