# CUI // SP-CTI
"""`git worktree add` is serialised across processes, and a killed add is cleaned up.

WHY (measured on the live host 2026-09-11, .logs/tools.genesis.reflexes.kanban.ndjson):

  * an add takes 5.1-25.3s ALONE (5.4, 5.1, 5.5, 12.8, 18.3, 23.2, 25.3) and
    34.2-35.9s whenever TWO OVERLAP, against WORKTREE_ADD_TIMEOUT_SECONDS = 30.
    Two dispatchers issue adds against one disk -- the genesis daemon's `kanban`
    reflex (`schedule: continuous`) and tools/genesis/kanban_scheduler.py -- and
    the per-task lease keeps them off the same CARD, not off the same DISK. The
    loser is killed and its task parked by `worktree-isolation-guard`; three
    tasks were parked that way in one evening and one needed a human.

  * `git worktree prune` SKIPS a LOCKED registration, and a killed add leaves
    exactly that. xrv-route-01 kept `locked initializing` with its directory
    already gone, so prune did nothing, `git branch -D` refused ("used by
    worktree at ..."), and orphan_requeue's empty-checkout act refused with
    `branch_delete_failed` until a human ran unlock + prune by hand.

Ruled out by measurement rather than argument, so that nobody re-derives it: the
dispatchers run at BelowNormal priority, and an A/B of six adds alternating
Normal/BelowNormal returned a 3.7s median for BOTH. Priority is not the cause.

Every repo here is a tmp_path fixture. A worktree test that reaches the live
roots removed four real worktrees on 2026-09-06; nothing in this file may touch
the shared checkout.
"""
from __future__ import annotations

import ast
import subprocess
import threading
import time
from pathlib import Path

import filelock  # declared in requirements.txt:159 -- never optional here

from tools.coordination import gitlock

_KANBAN = Path(__file__).resolve().parents[2] / "tools" / "genesis" / "reflexes" / "kanban.py"


# ── the lock itself ─────────────────────────────────────────────────────────


def test_worktree_add_lock_is_mutually_exclusive(tmp_path, monkeypatch):
    """Two holders never overlap. A thread lock would pass this and still
    collide across processes, so the assertion is on the FILE lock's behaviour
    with a real filelock, not on a mock."""
    monkeypatch.setattr(gitlock, "COORD_DIR", tmp_path)
    monkeypatch.setattr(gitlock, "_WORKTREE_ADD_LOCK_PATH", tmp_path / "wt.lock")

    spans: list[tuple[float, float]] = []
    lock = threading.Lock()

    def hold() -> None:
        with gitlock.worktree_add_lock(timeout=30) as held:
            assert held
            start = time.monotonic()
            time.sleep(0.4)
            with lock:
                spans.append((start, time.monotonic()))

    threads = [threading.Thread(target=hold) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(spans) == 3
    spans.sort()
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert next_start >= prev_end - 0.01, f"adds overlapped: {spans}"


def test_worktree_add_lock_yields_false_when_busy(tmp_path, monkeypatch):
    """A caller that cannot get the lock is told so -- it must not silently
    believe it holds one."""
    monkeypatch.setattr(gitlock, "COORD_DIR", tmp_path)
    monkeypatch.setattr(gitlock, "_WORKTREE_ADD_LOCK_PATH", tmp_path / "wt.lock")

    with gitlock.worktree_add_lock(timeout=5) as first:
        assert first is True
        done: list[bool] = []

        def contend() -> None:
            with gitlock.worktree_add_lock(timeout=0.2) as second:
                done.append(second)

        t = threading.Thread(target=contend)
        t.start()
        t.join(timeout=30)

    assert done == [False]


def test_worktree_add_lock_degrades_without_filelock(tmp_path, monkeypatch):
    """No filelock installed must not stop a dispatch. The lock is an
    optimisation against a measured collision, never a correctness boundary."""
    monkeypatch.setattr(gitlock, "COORD_DIR", tmp_path)
    monkeypatch.setattr(gitlock, "FileLock", None)
    with gitlock.worktree_add_lock(timeout=1) as held:
        assert held is True


def test_the_lock_is_a_cross_process_file_lock(tmp_path, monkeypatch):
    """The whole point: the two dispatchers are separate PROCESSES. A
    threading.Lock would pass every test above and collide in production, so
    assert the primitive itself, not just the behaviour."""
    monkeypatch.setattr(gitlock, "COORD_DIR", tmp_path)
    monkeypatch.setattr(gitlock, "_WORKTREE_ADD_LOCK_PATH", tmp_path / "wt.lock")
    assert gitlock.FileLock is filelock.FileLock
    with gitlock.worktree_add_lock(timeout=5) as held:
        assert held
        # A file lock leaves a file on disk for another PROCESS to contend on.
        assert (tmp_path / "wt.lock").exists()


def test_the_two_locks_are_different_files():
    """Sharing the commit lock would make every auto-commit wait behind a 25s
    checkout, and vice versa."""
    assert gitlock._WORKTREE_ADD_LOCK_PATH != gitlock._GIT_LOCK_PATH


# ── the cleanup a killed add owes ───────────────────────────────────────────


def test_remove_partial_worktree_unlocks_before_pruning(tmp_path, monkeypatch):
    """THE INCIDENT: prune skips a locked registration, so unlock must come
    first. Asserted on ORDER, because both commands running in the wrong order
    leaves the registration exactly as stuck as omitting one."""
    from tools.genesis.reflexes import kanban

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kanban.subprocess, "run", fake_run)
    wt = tmp_path / "gone"
    kanban._remove_partial_worktree(wt, "kanban/x", tmp_path)

    verbs = [c[1:3] for c in calls if c and c[0] == "git"]
    assert ["worktree", "unlock"] in verbs, f"no unlock: {verbs}"
    assert ["worktree", "prune"] in verbs, f"no prune: {verbs}"
    assert verbs.index(["worktree", "unlock"]) < verbs.index(["worktree", "prune"]), (
        f"unlock must precede prune, got {verbs}"
    )


# ── the wiring, read from source ────────────────────────────────────────────
# A behavioural test cannot see a future edit that moves the add back outside
# the lock: the add would still work, and the collision would only reappear on a
# loaded host under two dispatchers -- which is how this defect survived.


def _create_worktree_ast() -> ast.FunctionDef:
    tree = ast.parse(_KANBAN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_create_worktree":
            return node
    raise AssertionError("_create_worktree not found")


def test_the_add_takes_the_lock_before_it_runs():
    fn = _create_worktree_ast()
    src = ast.get_source_segment(_KANBAN.read_text(encoding="utf-8"), fn) or ""
    assert "worktree_add_lock" in src, "the add site does not take the lock"
    lock_at = src.index("worktree_add_lock")
    add_at = src.index('"worktree", "add"')
    assert lock_at < add_at, "the lock must be acquired BEFORE the add is spawned"
    assert "__exit__" in src, "the lock is never released"


def test_the_budget_is_unchanged():
    """The fix must not buy itself room by relaxing the budget -- that was
    forbidden by kph-repark-kph-repark-mfx-ci-04 and is pinned elsewhere."""
    from tools.genesis.reflexes import kanban

    assert kanban.WORKTREE_ADD_TIMEOUT_SECONDS == 30
    assert kanban.WORKTREE_ADD_LOCK_WAIT_SECONDS >= kanban.WORKTREE_ADD_TIMEOUT_SECONDS
