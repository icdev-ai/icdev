# CUI // SP-CTI
"""Serialize git auto-commits across concurrent sessions.

Sessions auto-commit ~every minute; two `git add && git commit` racing on the
same index corrupt each other (and clobber working-tree edits). Wrapping the
commit critical section in a repo-wide `filelock` makes them serialize.

Unlike `leases` (logical, TTL'd, span many processes), this is a genuine
OS-level lock held for the duration of a single commit within one process — the
correct tool for a short mutual-exclusion critical section.

    from tools.coordination.gitlock import repo_commit_lock
    with repo_commit_lock():
        subprocess.run(["git", "add", "-A"]); subprocess.run(["git", "commit", ...])
"""
from __future__ import annotations

import contextlib
from typing import Iterator

from tools.coordination.constants import COORD_DIR

try:
    from filelock import FileLock, Timeout as _FLTimeout
except Exception:  # pragma: no cover
    FileLock = None  # type: ignore[assignment]
    _FLTimeout = Exception  # type: ignore[assignment]

_GIT_LOCK_PATH = COORD_DIR / "git-repo.lock"
_WORKTREE_ADD_LOCK_PATH = COORD_DIR / "git-worktree-add.lock"


@contextlib.contextmanager
def repo_commit_lock(timeout: float = 60.0) -> Iterator[bool]:
    """Hold a repo-wide commit lock. Yields True when held.

    On timeout, yields False (caller should skip/retry rather than race). If
    filelock is unavailable, degrades to a no-op (yields True).
    """
    COORD_DIR.mkdir(parents=True, exist_ok=True)
    if FileLock is None:
        yield True
        return
    lock = FileLock(str(_GIT_LOCK_PATH), timeout=timeout)
    try:
        lock.acquire()
    except _FLTimeout:
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock.release()
        except Exception:
            pass


@contextlib.contextmanager
def worktree_add_lock(timeout: float = 60.0) -> Iterator[bool]:
    """Serialize ``git worktree add`` across every process on this checkout.

    Yields True when held, False on timeout (the caller should SKIP this cycle
    and leave the task schedulable -- "another add is running" is not a failed
    add, and must never be reported as one).

    WHY THIS EXISTS. Two dispatchers run adds against the same disk: the genesis
    daemon's ``kanban`` reflex (``schedule: continuous``) and the standalone
    ``tools/genesis/kanban_scheduler.py``. The per-task lease keeps them off the
    same card, not off the same disk -- the residual
    kph-repark-kph-repark-mfx-ci-04 named and did not fix. Measured on this host
    2026-09-11: an add takes 5.1-25.3s ALONE and 34.2-35.9s whenever two
    OVERLAP, against a 30s budget, so the loser is killed and its task parked.
    Three tasks were parked that way in one evening and one needed a human.

    A ``filelock`` under ``COORD_DIR`` is the right primitive for the same
    reason ``repo_commit_lock`` gives: it is an OS-level lock for a short
    mutual-exclusion critical section, and it spans processes because both
    dispatchers resolve the same COORD_DIR. A thread lock or a module-level flag
    would be blind to the other process -- it would pass a single-process test
    and collide in production, which is the shape this fix exists to remove.

    NOT a priority problem, and that was MEASURED rather than assumed: both
    dispatchers run BelowNormal, and an A/B of six adds alternating
    Normal/BelowNormal on an idle host returned a 3.7s median for BOTH. Do not
    "fix" a slow add by raising a priority class.
    """
    COORD_DIR.mkdir(parents=True, exist_ok=True)
    if FileLock is None:
        yield True
        return
    lock = FileLock(str(_WORKTREE_ADD_LOCK_PATH), timeout=timeout)
    try:
        lock.acquire()
    except _FLTimeout:
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock.release()
        except Exception:
            pass
