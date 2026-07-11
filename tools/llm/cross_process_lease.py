"""Cross-process LLM concurrency lease via OS file locks.

The in-process semaphore in :mod:`tools.llm.rate_gate` caps concurrent LLM calls
WITHIN a single process. When several ICDEV processes share one API key — the
dashboard, the kanban_scheduler, the genesis daemon — that per-process cap isn't
enough: N processes each allow ``max_parallel`` calls, so the API sees up to
N × max_parallel. This module adds a host-wide lease so the cap holds ACROSS
processes.

Why OS file locks (not a DB row): the lock is bound to an open file handle, so
if a holder crashes the OS releases it immediately — no stale lease, no reaper
thread, no DB round-trip on the LLM hot path, and it works fully air-gapped.

Scope: one host, same OS user (slot files live under a shared temp dir, override
with ``ICDEV_LLM_LEASE_DIR``). A multi-host global cap would need a networked
coordinator and is out of scope. Fail-open by contract: any lock-subsystem
problem returns ``None`` so the LLM call proceeds under the in-process cap alone
rather than hanging.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Optional

if os.name == "nt":  # pragma: no cover - platform-specific
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:  # pragma: no cover - platform-specific
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


def _lease_dir() -> Path:
    override = os.environ.get("ICDEV_LLM_LEASE_DIR", "").strip()
    base = Path(override) if override else Path(tempfile.gettempdir()) / "icdev_llm_leases"
    base.mkdir(parents=True, exist_ok=True)
    return base


class Lease:
    """A held cross-process slot. Call :meth:`release` (or use as a context manager)."""

    __slots__ = ("_fh",)

    def __init__(self, fh):
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            _unlock(fh)
            try:
                fh.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def acquire(
    name: str,
    max_slots: int,
    timeout: Optional[float] = None,
    poll: float = 0.05,
) -> Optional[Lease]:
    """Acquire one of *max_slots* host-wide slots named *name*.

    Blocks (polling every *poll* s) until a slot frees or *timeout* elapses.
    ``timeout=None`` waits indefinitely — safe here because OS file locks
    auto-release when a holder dies, so a slot can never be permanently stuck.

    Returns a :class:`Lease` (the caller MUST ``release()`` it), or ``None`` on
    timeout or any lock-subsystem failure. ``None`` means "proceed with only the
    in-process cap" — fail-open, never hang the LLM call.
    """
    if max_slots <= 0:
        return None
    try:
        d = _lease_dir()
    except OSError:
        return None  # can't create lease dir — degrade to in-process only

    deadline = None if timeout is None else (time.monotonic() + max(0.0, timeout))
    while True:
        for i in range(max_slots):
            path = d / f"{name}.slot{i}"
            try:
                fh = open(path, "a+")  # noqa: SIM115 — handle lives as long as the lease
            except OSError:
                continue
            if _try_lock(fh):
                return Lease(fh)
            try:
                fh.close()
            except OSError:
                pass
        if deadline is not None and time.monotonic() >= deadline:
            return None
        time.sleep(poll)
