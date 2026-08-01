# CUI // SP-CTI
"""OPT-72: tests for tools/kanban/state_machine.py."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import state_machine as sm  # noqa: E402
from tools.kanban.state_machine import KanbanState  # noqa: E402


def test_enum_values_match_schema_allowed_statuses():
    """db_status_for() must only ever return a status the CHECK constraint allows.

    The allowed set is IMPORTED from migration 260, which is the source of truth
    for the kanban_tasks.status CHECK -- per CLAUDE.md, CHECK constraints are
    derived from Python constants, never hardcoded. This test previously carried
    its own stale copy of the pre-260 list and so failed once the lifecycle
    statuses (pr_opened/ci_failed/...) stopped collapsing to in_progress.
    """
    import importlib

    mig = importlib.import_module(
        "tools.db.migrations.260_kanban_status_lifecycle.up"
    )
    allowed = set(mig.STATUS_VALUES)

    for state in KanbanState:
        assert sm.db_status_for(state) in allowed


def test_can_transition_happy_path():
    assert sm.can_transition(KanbanState.BACKLOG, KanbanState.SCHEDULED)
    assert sm.can_transition(KanbanState.SCHEDULED, KanbanState.IN_PROGRESS)
    assert sm.can_transition(KanbanState.IN_PROGRESS, KanbanState.PR_OPENED)
    assert sm.can_transition(KanbanState.PR_OPENED, KanbanState.DONE)


def test_can_transition_rejects_illegal_pair():
    assert not sm.can_transition(KanbanState.BACKLOG, KanbanState.DONE)
    assert not sm.can_transition(KanbanState.DONE, KanbanState.IN_PROGRESS)
    assert not sm.can_transition(
        KanbanState.SUGGESTED, KanbanState.IN_PROGRESS
    )


def test_transition_same_state_is_no_op():
    r = sm.transition(
        "task-1", KanbanState.IN_PROGRESS, KanbanState.IN_PROGRESS,
    )
    assert r.applied is False
    assert r.label == "noop"


def test_transition_illegal_raises():
    with pytest.raises(sm.InvalidTransition):
        sm.transition("task-x", KanbanState.BACKLOG, KanbanState.DONE)


def test_transition_happy_writes_audit_and_db():
    db_calls = []
    audit_calls = []

    def fake_db(sql, params):
        db_calls.append((sql, params))

    def fake_audit(sql, params):
        audit_calls.append((sql, params))

    r = sm.transition(
        "task-xyz", KanbanState.IN_PROGRESS, KanbanState.PR_OPENED,
        reason="agent opened PR",
        actor="test",
        db_exec=fake_db,
        audit_exec=fake_audit,
    )
    assert r.applied is True
    assert r.label == "pr_opened"
    # Migration 260 widened the CHECK: pr_opened persists as itself rather than
    # collapsing to in_progress, so "PR open, awaiting merge" is visible on the
    # board and is not counted as active work by the dispatcher.
    assert r.db_status == "pr_opened"
    assert r.audit_written is True

    assert len(db_calls) == 1
    assert "UPDATE kanban_tasks" in db_calls[0][0]
    assert db_calls[0][1][0] == "pr_opened"

    assert len(audit_calls) == 1
    details = json.loads(audit_calls[0][1][4])
    assert details["from_state"] == "in_progress"
    assert details["to_state"] == "pr_opened"
    assert details["workflow_state"] == "pr_opened"


def test_resume_cycle_cap_enforced():
    # At cycle MAX, resume should be rejected
    with pytest.raises(sm.ResumeCycleExceeded):
        sm.transition(
            "task-loop",
            KanbanState.CI_FAILED,
            KanbanState.IN_PROGRESS,
            resume_cycle=sm.MAX_RESUME_CYCLES,
        )
    # One below MAX should still work
    r = sm.transition(
        "task-loop",
        KanbanState.CI_FAILED,
        KanbanState.IN_PROGRESS,
        resume_cycle=sm.MAX_RESUME_CYCLES - 1,
    )
    assert r.applied is True


def test_valid_next_states_from_pr_opened():
    options = set(sm.valid_next_states(KanbanState.PR_OPENED))
    assert KanbanState.DONE in options
    assert KanbanState.CI_FAILED in options
    assert KanbanState.MERGE_CONFLICT in options
    assert KanbanState.CHANGES_REQUESTED in options
    assert KanbanState.FAILED in options
    # IN_PROGRESS is NOT directly reachable — must go via a resumable
    assert KanbanState.IN_PROGRESS not in options


def test_is_terminal():
    assert sm.is_terminal(KanbanState.DONE)
    assert sm.is_terminal(KanbanState.FAILED)
    assert not sm.is_terminal(KanbanState.IN_PROGRESS)
    assert not sm.is_terminal(KanbanState.PR_OPENED)


def test_parse_state_handles_unknown_and_none():
    assert sm.parse_state("in_progress") == KanbanState.IN_PROGRESS
    assert sm.parse_state("IN_PROGRESS") == KanbanState.IN_PROGRESS
    assert sm.parse_state(None) is None
    assert sm.parse_state("") is None
    assert sm.parse_state("mystery") is None


def test_failed_state_persists_as_failed_not_token_exhausted():
    """FAILED must persist as 'failed', not masquerade as a resumable pause.

    Before migration 260 it collapsed to token_exhausted, which meant a genuine
    failure looked like an out-of-tokens pause and got auto-resumed.
    """
    assert sm.db_status_for(KanbanState.FAILED) == "failed"
    assert sm.db_status_for(KanbanState.TOKEN_EXHAUSTED) == "token_exhausted"


def test_transition_without_db_exec_still_validates():
    r = sm.transition(
        "task-2", KanbanState.BACKLOG, KanbanState.SCHEDULED,
    )
    assert r.applied is True
    assert r.label == "schedule"
