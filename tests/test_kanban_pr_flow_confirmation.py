# CUI // SP-CTI
"""A `gh` timeout must not throw pushed work back to `backlog`.

`_has_open_pr` returns False on ANY error — a 10s timeout, a rate limit, an auth
blip. That is correct for the job it was written for: a respawn guard at line
6271, where False means "go ahead and dispatch" and the cost of being wrong is
one extra dispatch.

It is reused to answer the OPPOSITE question after a branch is pushed: "did the
PR open?" There False means "throw the work back to `backlog`", and the cost of
being wrong is real commits sitting on a pushed branch while the board says the
task never started. The scheduler then dispatches it again, which is where
duplicate PRs on one branch come from.

MEASURED on kanban_status_transitions: `PR flow: branch pushed but the PR could
not be opened` is **66 of the 126 backwards transitions** — the single largest
cause of a task going backwards, more than orphan_sweep, the stale reaper and
auto-revive combined.

The distinction already exists in this module. `_open_pr_listing_unavailable`
was added for the reaper with exactly this reasoning: "'no evidence of a PR' and
'could not look for a PR' lead to opposite decisions about a task's fate." This
extends that seam to the second caller rather than writing a second copy of it.
"""
from __future__ import annotations

import subprocess

import tools.genesis.reflexes.kanban as km


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _runner(proc=None, raises=None):
    def run(cmd, **kw):
        if raises:
            raise raises
        return proc
    return run


# ── the three states ────────────────────────────────────────────────────────
def test_a_listed_pr_is_OPEN(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _runner(_Proc(0, '[{"number": 1744}]')))
    assert km._pr_open_state("t-1") == km.PR_OPEN


def test_an_empty_listing_is_NONE(monkeypatch):
    """gh answered, and the answer is that there is no PR. That is a real
    finding and the rollback is correct."""
    monkeypatch.setattr(subprocess, "run", _runner(_Proc(0, "[]")))
    assert km._pr_open_state("t-1") == km.PR_NONE


def test_a_timeout_is_UNKNOWN(monkeypatch):
    """The case that cost 66 rollbacks. A slow `gh` is not evidence of anything."""
    monkeypatch.setattr(
        subprocess, "run", _runner(raises=subprocess.TimeoutExpired("gh", 10)))
    assert km._pr_open_state("t-1") == km.PR_UNKNOWN


def test_a_nonzero_exit_is_UNKNOWN(monkeypatch):
    """Unauthenticated, rate-limited, no remote — all 'could not ask'."""
    monkeypatch.setattr(
        subprocess, "run", _runner(_Proc(1, "", "gh: could not authenticate")))
    assert km._pr_open_state("t-1") == km.PR_UNKNOWN


def test_gh_missing_is_UNKNOWN(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _runner(raises=OSError("no gh")))
    assert km._pr_open_state("t-1") == km.PR_UNKNOWN


def test_unparseable_output_is_UNKNOWN(monkeypatch):
    """Output we cannot read is not an empty list."""
    monkeypatch.setattr(subprocess, "run", _runner(_Proc(0, "not json")))
    assert km._pr_open_state("t-1") == km.PR_UNKNOWN


def test_an_empty_string_is_UNKNOWN(monkeypatch):
    """gh exiting 0 with no stdout told us nothing; the old code read this as
    'no PR' because it only checked truthiness of stdout."""
    monkeypatch.setattr(subprocess, "run", _runner(_Proc(0, "   ")))
    assert km._pr_open_state("t-1") == km.PR_UNKNOWN


# ── the respawn guard keeps its exact behaviour ────────────────────────────
def test_the_respawn_guard_blocks_only_on_a_CONFIRMED_pr(monkeypatch):
    """Unchanged on purpose. There False means 'dispatch', and the cost of being
    wrong is one extra dispatch — the opposite trade from the PR-flow caller."""
    monkeypatch.setattr(subprocess, "run", _runner(_Proc(0, '[{"number": 1}]')))
    assert km._has_open_pr("t-1") is True


def test_the_respawn_guard_still_fails_open_to_dispatch(monkeypatch):
    for runner in (_runner(raises=OSError("no gh")),
                   _runner(_Proc(1, "")),
                   _runner(_Proc(0, "[]"))):
        monkeypatch.setattr(subprocess, "run", runner)
        assert km._has_open_pr("t-1") is False


# ── what the PR-flow caller does with each state ───────────────────────────
def test_open_moves_the_task_to_pr_opened():
    status, reason = km._pr_flow_outcome(km.PR_OPEN)
    assert status == "pr_opened"
    assert reason


def test_none_rolls_back_and_says_why():
    """The genuine failure the rollback exists for is preserved."""
    status, reason = km._pr_flow_outcome(km.PR_NONE)
    assert status == "backlog"
    assert "could not be opened" in reason


def test_unknown_moves_the_task_NOWHERE():
    """The fix. Pushed commits are not thrown away because `gh` was slow — the
    task is left where it is, and the stale reaper remains the backstop for one
    that genuinely died."""
    status, reason = km._pr_flow_outcome(km.PR_UNKNOWN)
    assert status is None, "an unverifiable answer must not move the task"
    assert reason, "and it must still say something, or the silence hides it"


def test_unknown_is_not_silent():
    """A rollback that stops happening must not become a task that quietly sits.
    The reason names the ambiguity so a human reading the log can tell this from
    a task nobody dispatched."""
    _, reason = km._pr_flow_outcome(km.PR_UNKNOWN)
    lowered = reason.lower()
    assert "could not" in lowered or "unknown" in lowered or "unverif" in lowered


def test_every_state_is_handled():
    """A fourth state added later must not fall through to a silent no-op."""
    for state in (km.PR_OPEN, km.PR_NONE, km.PR_UNKNOWN):
        status, reason = km._pr_flow_outcome(state)
        assert reason, f"{state} produced no reason"
    assert {km.PR_OPEN, km.PR_NONE, km.PR_UNKNOWN} == {"open", "none", "unknown"}


# ── the call site actually uses it ─────────────────────────────────────────
def test_the_pr_flow_branch_consults_the_three_state_helper():
    """Structural: the whole point is that this ONE call site stopped using the
    boolean. If it reverts to `_has_open_pr`, the 66-event defect is back and
    every test above still passes."""
    import inspect

    src = inspect.getsource(km)
    # Anchor on the call site itself. An earlier version of this test anchored
    # on the rollback REASON string, which now also appears inside
    # `_pr_flow_outcome` — so it matched the helper's own docstring and proved
    # nothing about the caller.
    i = src.index("if _pr_flow_enabled() and has_commits:")
    window = src[i:i + 1600]
    assert "_pr_open_state(" in window, (
        "the PR-flow confirmation must ask for three states, not a boolean")
    assert "_pr_flow_outcome(" in window
    assert "_has_open_pr(" not in window, (
        "the boolean is the defect — this caller must not reach for it again")


def test_the_respawn_guard_still_uses_the_boolean():
    """And the guard must NOT be switched to the three-state helper — its
    fail-open-to-dispatch default is correct for what it does."""
    import inspect

    src = inspect.getsource(km)
    i = src.index("Respawn guard: open PR found for")
    window = src[max(0, i - 600):i]
    assert "_has_open_pr(" in window
