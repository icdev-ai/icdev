# CUI // SP-CTI
"""Named resource leases for cross-session coordination.

A *lease* is a logical claim on a resource (`service:dashboard`, `file:tools/x.py`,
`git:repo`, `migration:schema`) held by a *session* (not an OS process) for a
TTL. Other sessions can see who holds what. This is what prevents two sessions
from overwriting the same file or both restarting the dashboard.

Implementation: each resource has a metadata JSON in
``.tmp/coordination/leases/`` describing the holder; a short-lived `filelock`
(cross-platform, Windows-safe) makes the read-modify-write atomic across
processes. The OS lock is NOT held for the lease lifetime — the lease persists
as metadata + TTL, so it survives across the many short-lived processes that
make up one agent session.

Enforcement is hybrid (see HARD_NAMESPACES): service/git/migration are
acquire-or-refuse; file is advisory (acquire always succeeds, but returns the
prior holder so the caller can WARN).

    from tools.coordination import leases
    h = leases.acquire("service:dashboard", intent="restart", block=True)
    if h: ... ; h.release()
    who = leases.holder("file:tools/x.py")   # None or holder dict
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from tools.coordination.constants import (
    FILE_LEASE_TTL_SECONDS,
    HARD_NAMESPACES,
    LEASE_DIR,
    get_agent_type,
    get_session_id,
)

try:
    from filelock import FileLock, Timeout as _FLTimeout
except Exception:  # pragma: no cover
    FileLock = None  # type: ignore[assignment]
    _FLTimeout = Exception  # type: ignore[assignment]


def _now() -> float:
    # time.time() is allowed; avoids the Date.now-style restriction on new Date().
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _paths(resource: str):
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(resource.encode("utf-8")).hexdigest()[:16]
    safe = "".join(c if c.isalnum() else "_" for c in resource)[:40]
    stem = f"{safe}-{h}"
    return LEASE_DIR / f"{stem}.lock", LEASE_DIR / f"{stem}.json"


def _namespace(resource: str) -> str:
    return resource.split(":", 1)[0] if ":" in resource else resource


def _read_meta(meta_path) -> Optional[Dict[str, Any]]:
    try:
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _is_expired(meta: Dict[str, Any]) -> bool:
    try:
        return _now() > float(meta.get("acquired_at_ts", 0)) + float(meta.get("ttl_seconds", 0))
    except Exception:
        return True


class Lease:
    """Handle for a held lease. Call release() (or use as a context manager)."""

    def __init__(self, resource: str, prior_holder: Optional[Dict[str, Any]] = None):
        self.resource = resource
        self.prior_holder = prior_holder  # for warn-only (file) leases

    def release(self) -> None:
        release(self.resource)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def holder(resource: str) -> Optional[Dict[str, Any]]:
    """Return the current (non-expired) holder metadata, or None if free."""
    _, meta_path = _paths(resource)
    meta = _read_meta(meta_path)
    if not meta or _is_expired(meta):
        return None
    return meta


def holder_is_alive(resource: str) -> Optional[bool]:
    """Is the process that took this lease still running?

    ``None`` when there is no holder or the answer cannot be determined — the
    caller must treat that as "assume alive" rather than reclaim on ignorance.

    Guards against PID reuse: a process whose start time is LATER than the
    lease's ``acquired_at_ts`` is a different process that inherited the number,
    so the original holder is gone.
    """
    meta = holder(resource)
    if not meta:
        return None
    pid = meta.get("pid")
    if not isinstance(pid, int):
        return None
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        acquired = meta.get("acquired_at_ts")
        if isinstance(acquired, (int, float)):
            try:
                if psutil.Process(pid).create_time() > float(acquired) + 1.0:
                    return False  # PID reused by a newer process
            except Exception:
                pass
        return True
    except ImportError:
        pass
    try:
        from tools.compat.platform_utils import pid_exists
        return bool(pid_exists(pid))
    except Exception:
        return None


def release_stale(resource: str) -> bool:
    """Release a lease whose holding PROCESS is gone, whoever took it.

    :func:`release` matches on ``holder_session``, which is correct for a lease
    guarding concurrent work. It is unusable for an operator toggle that is
    *meant* to outlive its process: ``kanban --pause-runner`` takes the lease and
    exits immediately, and every later CLI invocation derives a NEW session id
    (``get_session_id`` returns a fresh ``local-<uuid>`` when no CLAUDE_SESSION_ID
    is exported). So ``--resume-runner`` could never match its own ``--pause-runner``
    and reported "NOT PAUSED BY THIS SESSION" — the pause was releasable only by
    waiting out its 4-hour TTL.

    Refuses while the holder is alive, so one live session cannot steal the pause
    another live session is relying on.
    """
    alive = holder_is_alive(resource)
    if alive is not False:
        return False  # no holder, still running, or unknowable -> do not reclaim
    _, meta_path = _paths(resource)

    def _do() -> bool:
        cur = _read_meta(meta_path)
        if not cur:
            return False
        try:
            meta_path.unlink()
        except Exception:
            return False
        return True

    lock_path, _ = _paths(resource)
    if FileLock is not None:
        try:
            with FileLock(str(lock_path), timeout=5):
                return _do()
        except Exception:
            return _do()
    return _do()


def acquire(
    resource: str,
    intent: str = "",
    ttl_seconds: Optional[int] = None,
    block: bool = False,
    block_timeout: float = 30.0,
) -> Optional[Lease]:
    """Claim a resource lease for the current session.

    HARD namespaces (service/git/migration): if held by ANOTHER live session,
    either wait (block=True, up to block_timeout) or return None (refuse).
    SOFT namespaces (file): always succeeds; the returned Lease carries
    `prior_holder` so the caller can warn the user.
    """
    sid = get_session_id()
    ns = _namespace(resource)
    ttl = int(ttl_seconds if ttl_seconds is not None else FILE_LEASE_TTL_SECONDS)
    lock_path, meta_path = _paths(resource)
    hard = ns in HARD_NAMESPACES
    deadline = _now() + block_timeout

    def _attempt() -> Optional[Lease]:
        cur = _read_meta(meta_path)
        prior = cur if (cur and not _is_expired(cur)) else None
        if hard and prior and prior.get("holder_session") != sid:
            return None  # held by someone else
        meta = {
            "resource": resource,
            "holder_session": sid,
            "holder_agent": get_agent_type(),
            "pid": os.getpid(),
            "intent": intent,
            "acquired_at_ts": _now(),
            "acquired_at": _iso(_now()),
            "ttl_seconds": ttl,
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        # For soft leases, surface the prior holder (if a different session) so
        # the caller can warn — but we still record our own claim.
        warn_holder = prior if (prior and prior.get("holder_session") != sid) else None
        return Lease(resource, prior_holder=warn_holder)

    # filelock makes the read-modify-write atomic across processes.
    while True:
        result: Optional[Lease] = None
        if FileLock is not None:
            try:
                with FileLock(str(lock_path), timeout=5):
                    result = _attempt()
            except _FLTimeout:
                result = None
            except Exception:
                result = _attempt()  # degrade: best-effort without the OS lock
        else:
            result = _attempt()

        if result is not None:
            return result
        if not (hard and block) or _now() >= deadline:
            return None
        time.sleep(0.5)


def release(resource: str) -> bool:
    """Release a lease IF the current session holds it."""
    sid = get_session_id()
    lock_path, meta_path = _paths(resource)

    def _do() -> bool:
        cur = _read_meta(meta_path)
        if cur and cur.get("holder_session") == sid:
            try:
                meta_path.unlink()
            except Exception:
                pass
            return True
        return False

    if FileLock is not None:
        try:
            with FileLock(str(lock_path), timeout=5):
                return _do()
        except Exception:
            return _do()
    return _do()


def release_all_for_session(session_id: Optional[str] = None) -> int:
    """Release every lease held by a session (called from the Stop hook)."""
    sid = session_id or get_session_id()
    released = 0
    try:
        for meta_path in LEASE_DIR.glob("*.json"):
            meta = _read_meta(meta_path)
            if meta and meta.get("holder_session") == sid:
                try:
                    meta_path.unlink()
                    released += 1
                except Exception:
                    pass
    except Exception:
        pass
    return released


def list_leases() -> list:
    """All currently-held (non-expired) leases."""
    out = []
    try:
        for meta_path in LEASE_DIR.glob("*.json"):
            meta = _read_meta(meta_path)
            if meta and not _is_expired(meta):
                out.append(meta)
    except Exception:
        pass
    return out
