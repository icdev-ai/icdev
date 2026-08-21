# CUI // SP-CTI
"""A task that cannot get a worktree must not build in the SHARED CHECKOUT (autonomy-adm-02).

THE DEFECT. ``_dispatch_to_claude`` ended its worktree block with::

    work_dir = worktree_path if worktree_path else str(BASE_DIR)

For an EXTERNAL task the branch immediately above already refused that fallback —
its comment calls BASE_DIR "the whole hazard this change exists to remove". The
INTERNAL branch was left open: it printed one line and dispatched an autonomous
worker into ``BASE_DIR``, the shared working directory that concurrent sessions
share. That is this repo's cardinal rule ("NEVER work on the shared checkout")
being broken by its own dispatcher, and it fired on 2026-08-20 for rem-hyg-18.

THE HARM IS NOT HYPOTHETICAL, and it is already documented one screen up in the
same module: ``_create_worktree``'s stale-branch cleanup exists because a failure
there was "forcing every subsequent dispatch into BASE_DIR — causing the coherence
loop". The fallback has caused a real incident. Separately, a second session's
``git checkout`` moves HEAD under the worker, so commits land on the wrong branch
and pushes get clobbered.

WHY A STRUCTURAL TEST WOULD NOT DO. Reading the source for the absence of
``BASE_DIR`` pins one expression and nothing else — the lesson of the studio park
race, where one park site was fixed, its structural test read that function's
source, and an identical second site failed for weeks. These tests drive
``_dispatch_to_claude`` and assert on what it DID: the task parked, and the
executor chain was never reached.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402
from tools.kanban.repo_registry import RepoTarget  # noqa: E402


class _Recorder:
    """Captures the two things that decide whether the fallback fired."""

    def __init__(self):
        self.moves: list[tuple[str, str, str]] = []   # (task_id, status, reason)
        self.dispatched: list = []                    # non-empty => it kept going

    @property
    def parked(self) -> bool:
        return bool(self.moves)


def _arm(monkeypatch, *, worktree, external=False) -> _Recorder:
    """Stub the path up to the worktree guard and record what happens after it."""
    rec = _Recorder()

    target = None
    if external:
        # The REAL RepoTarget, never a stand-in: `dispatchable` is a property
        # derived from `root`, so a hand-rolled double can satisfy this test
        # while diverging from what the dispatcher actually receives.
        target = RepoTarget(
            name="compass",
            root=Path("/tmp/compass"),
            base_branch="main",
            is_external=True,
        )

    monkeypatch.setattr(k, "_manual_build", lambda: False)
    monkeypatch.setattr(k, "_task_repo_target", lambda _tid: target)
    monkeypatch.setattr(k, "_pre_dispatch_check", lambda _t: (False, ""))
    monkeypatch.setattr(k, "_create_worktree", lambda _tid: worktree)
    monkeypatch.setattr(
        k, "_move_task",
        lambda tid, status, **kw: rec.moves.append((tid, status, kw.get("reason") or "")),
    )
    # The first thing the dispatch does AFTER the guard. Reaching it means the
    # guard let a worker through; raising keeps the test from spawning anything.
    def _boom(chain):
        rec.dispatched.append(chain)
        raise RuntimeError("dispatch proceeded past the worktree guard")

    monkeypatch.setattr(k, "_build_effective_executor_chain", _boom)
    return rec


def _run(prompt: Path, task_id: str = "autonomy-probe-01") -> None:
    task = {"id": task_id, "title": "probe", "failure_count": 0, "max_retries": 5}
    try:
        k._dispatch_to_claude(task, str(prompt))
    except RuntimeError as exc:                       # only _boom raises this
        if "proceeded past the worktree guard" not in str(exc):
            raise


@pytest.fixture()
def prompt(tmp_path) -> Path:
    p = tmp_path / "prompt.md"
    p.write_text("build the thing", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 1. The gap: an INTERNAL task whose worktree could not be created
# --------------------------------------------------------------------------- #
def test_an_internal_task_never_builds_in_the_shared_checkout(monkeypatch, prompt):
    """rem-hyg-18's exact shape on 2026-08-20."""
    rec = _arm(monkeypatch, worktree=None, external=False)
    _run(prompt)

    assert not rec.dispatched, (
        "an autonomous worker was dispatched into BASE_DIR — the shared checkout "
        "that concurrent sessions share"
    )
    assert rec.parked, "the task was neither built nor parked; the dispatch vanished"


def test_the_park_reason_names_the_worktree_as_the_cause(monkeypatch, prompt):
    """A park with no reason is indistinguishable from work nobody looked at.
    The next reader must be able to tell 'could not isolate' from 'refused'."""
    rec = _arm(monkeypatch, worktree=None, external=False)
    _run(prompt)

    _tid, _status, reason = rec.moves[0]
    assert "worktree" in reason.lower(), reason


# --------------------------------------------------------------------------- #
# 2. What must NOT change
# --------------------------------------------------------------------------- #
def test_a_task_with_a_worktree_still_dispatches(monkeypatch, prompt, tmp_path):
    """The guard fires on ABSENCE. A healthy dispatch is untouched — otherwise
    this fix trades a shared-checkout hazard for a board that never builds."""
    wt = tmp_path / "wt"
    wt.mkdir()
    rec = _arm(monkeypatch, worktree=str(wt), external=False)
    _run(prompt)

    assert rec.dispatched, "a task WITH a worktree must still reach the executor"
    assert not rec.parked, "a healthy dispatch must not be parked"


def test_an_external_task_still_parks(monkeypatch, prompt):
    """The pre-existing external refusal keeps working. Widening the guard to
    every task must not delete the narrower one it grew out of."""
    rec = _arm(monkeypatch, worktree=None, external=True)
    _run(prompt)

    assert not rec.dispatched
    assert rec.parked


# --------------------------------------------------------------------------- #
# 3. Fail-closed, not fail-quiet
# --------------------------------------------------------------------------- #
def test_a_park_that_itself_fails_still_refuses_to_dispatch(monkeypatch, prompt):
    """If the board write fails, the SAFE outcome is still not-dispatching.
    A guard that dispatches when its own bookkeeping breaks is not a guard."""
    rec = _arm(monkeypatch, worktree=None, external=False)

    def _explode(*_a, **_kw):
        raise RuntimeError("board unreachable")

    monkeypatch.setattr(k, "_move_task", _explode)
    _run(prompt)

    assert not rec.dispatched, (
        "the dispatch proceeded into the shared checkout because the PARK failed — "
        "fail-closed means the worker still must not run"
    )
