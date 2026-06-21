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
