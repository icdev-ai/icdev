#!/usr/bin/env python3
"""A timeout must not re-dispatch work that already exists. CUI // SP-CTI

The dispatch timeout handler demoted a task to `scheduled` on any overrun,
skipping only when the status was already `done`. A session that finished the
work, opened its PR and THEN overran the budget was therefore sent back to be
built again from scratch.

Observed 2026-08-15 on trust-struct-03: PR #1679 was open with EVERY check
passing — E2E included — while the board had the task in `scheduled` with a
failure counted against it. Nothing reported the contradiction. A board saying
"retry this" and a forge saying "this is done" are equally confident, and only
one of them is right.

Two ways the work can already exist, and the handler now checks both:
  * the status says so (`done`, `pr_opened`, `merged`), or
  * the STATUS was never updated but commits sit on the task's branch — which,
    when a session dies mid-flight, is the only evidence left.

Deterministic: the branch probe is injected. No git, no network, no board.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


@pytest.fixture
def no_branch(monkeypatch):
    monkeypatch.setattr(k, "_branch_has_unmerged_commits", lambda _t: False)


@pytest.fixture
def has_branch(monkeypatch):
    monkeypatch.setattr(k, "_branch_has_unmerged_commits", lambda _t: True)


# --------------------------------------------------------------------------- #
# The regression this exists for
# --------------------------------------------------------------------------- #

def test_pr_opened_is_not_re_dispatched(no_branch):
    """THE bug. A session whose PR is up has finished; the timeout is an overrun."""
    assert k.timeout_demotion_skip_reason("t-1", "pr_opened")


def test_commits_on_the_branch_block_demotion_even_when_the_status_is_stale(has_branch):
    """The status is not always updated — a session can die before it reports.

    The branch is then the only evidence the work happened, and it is the same
    merge-verification primitive the done-gate uses, so dispatch and completion
    agree on what "there is work here" means.
    """
    assert k.timeout_demotion_skip_reason("t-2", "in_progress")
    assert k.timeout_demotion_skip_reason("t-3", "scheduled")


def test_done_still_skips(no_branch):
    """The pre-existing guard must survive the change."""
    assert k.timeout_demotion_skip_reason("t-4", "done")


# --------------------------------------------------------------------------- #
# It must still demote a genuinely stuck task
# --------------------------------------------------------------------------- #

def test_a_task_with_no_work_is_demoted_normally(no_branch):
    """The guard must not become "never retry anything".

    A task that timed out having produced nothing is exactly what the retry
    path is for.
    """
    assert k.timeout_demotion_skip_reason("t-5", "in_progress") == ""
    assert k.timeout_demotion_skip_reason("t-6", "scheduled") == ""


@pytest.mark.parametrize("status", ["", "backlog", "failed", "needs_decomposition"])
def test_other_statuses_with_no_branch_are_demoted(status, no_branch):
    assert k.timeout_demotion_skip_reason("t-7", status) == ""


# --------------------------------------------------------------------------- #
# Fail-open — an unreachable git must never wedge the scheduler
# --------------------------------------------------------------------------- #

def test_branch_probe_failure_falls_back_to_normal_demotion(monkeypatch):
    """A broken git makes the guard silent, not sticky.

    The opposite choice — treating an error as "work might exist" — would stop
    every timed-out task from ever being retried the moment git hiccuped.
    """
    def _boom(_task_id):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(k, "_branch_has_unmerged_commits", _boom)
    assert k.timeout_demotion_skip_reason("t-8", "in_progress") == ""


def test_a_terminal_status_skips_without_consulting_git(monkeypatch):
    """Cheap check first: a known status must not pay for a subprocess."""
    def _fail(_task_id):
        raise AssertionError("branch probe must not run for a terminal status")

    monkeypatch.setattr(k, "_branch_has_unmerged_commits", _fail)
    assert k.timeout_demotion_skip_reason("t-9", "done")
    assert k.timeout_demotion_skip_reason("t-10", "pr_opened")


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #

def test_the_reason_is_human_readable_not_just_true():
    """It is logged, and "why was this not retried" is the operator's question."""
    reason = k.timeout_demotion_skip_reason("t-11", "pr_opened")
    assert "pr_opened" in reason


def test_pr_opened_is_declared_in_the_no_demote_vocabulary():
    assert "pr_opened" in k._TIMEOUT_NO_DEMOTE_STATUSES
    assert "done" in k._TIMEOUT_NO_DEMOTE_STATUSES


def test_the_handler_actually_calls_the_helper():
    """A guard the handler does not consult is the same bug with extra steps."""
    import inspect

    src = inspect.getsource(k)
    assert "timeout_demotion_skip_reason(task_id, _cur_status)" in src
