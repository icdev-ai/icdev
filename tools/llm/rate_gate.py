"""Rate-limited LLM mode: a process-global concurrency gate + inter-call pause.

For environments with strict LLM rate limits (only one call at a time). When
enabled, every provider call — TEXT and VISION alike, since both funnel through
the router's single provider-invoke choke point — is serialized through a shared
semaphore, and each call is followed by a randomized pause before the next may
start, guaranteeing a minimum gap between calls.

OFF by default: when disabled, :func:`rate_gate` is a zero-overhead no-op and the
router behaves exactly as before.

Toggle (env wins over ``args/llm_config.yaml`` ``rate_limit``):
  ICDEV_LLM_RATE_LIMIT = true|false   master toggle
  ICDEV_LLM_MAX_PARALLEL = <int>       concurrent in-flight calls (default 1)
  ICDEV_LLM_PAUSE_MIN = <float seconds> inter-call pause lower bound (default 3)
  ICDEV_LLM_PAUSE_MAX = <float seconds> inter-call pause upper bound (default 5)

The gate is module-global (shared across every ``LLMRouter`` instance and thread,
e.g. council/debate ``ThreadPoolExecutor`` workers) so the cap holds in-process
regardless of how many callers exist.

By default the cap is per-process. ``rate_limit.scope``
(``ICDEV_LLM_RATE_LIMIT_SCOPE``) widens it:
  * ``global``/``host`` — ALSO hold a host-wide OS file-lock lease
    (:mod:`tools.llm.cross_process_lease`) so the cap holds across processes on
    one host (dashboard + kanban_scheduler + genesis daemon).
  * ``cluster``/``multi-host`` — ALSO hold a PostgreSQL advisory-lock lease
    (:mod:`tools.llm.pg_lease`) so the cap holds across every host sharing the
    same database.
Either lease fails open — if the lock subsystem or DB is unavailable the call
proceeds under the in-process cap alone rather than hanging.
"""

from __future__ import annotations

import os
import random
import threading
import time
from contextlib import contextmanager
from typing import Optional, Tuple

try:
    from tools.llm import cross_process_lease as _cpl
    from tools.llm import pg_lease as _pgl
except ImportError:  # packaged-only install
    from icdev.tools.llm import cross_process_lease as _cpl
    from icdev.tools.llm import pg_lease as _pgl

# Shared across all router instances/threads in this process.
_GATE_LOCK = threading.Lock()
_GATE: threading.BoundedSemaphore | None = None
_GATE_LIMIT: int | None = None

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return fallback


def resolve_rate_limit(config: dict | None) -> Tuple[int, float, float]:
    """Resolve ``(max_parallel, pause_min, pause_max)`` for rate-limited mode.

    ``max_parallel <= 0`` means the feature is OFF (no gate, no pause). Env
    overrides win over the ``rate_limit`` block of ``config`` so a constrained
    environment can toggle without editing YAML.
    """
    rl = {}
    if isinstance(config, dict):
        rl = config.get("rate_limit") or {}

    env = os.environ.get("ICDEV_LLM_RATE_LIMIT", "").strip().lower()
    if env in _TRUE:
        enabled = True
    elif env in _FALSE:
        enabled = False
    else:
        enabled = bool(rl.get("enabled", False))
    if not enabled:
        return 0, 0.0, 0.0

    # max_parallel: env > config > default 1; never below 1 when enabled.
    max_parallel = 1
    env_mp = os.environ.get("ICDEV_LLM_MAX_PARALLEL", "").strip()
    if env_mp:
        try:
            max_parallel = int(env_mp)
        except ValueError:
            max_parallel = 1
    else:
        try:
            max_parallel = int(rl.get("max_parallel", 1))
        except (TypeError, ValueError):
            max_parallel = 1
    if max_parallel < 1:
        max_parallel = 1

    pause_min = _env_float("ICDEV_LLM_PAUSE_MIN", _cfg_float(rl, "pause_min_seconds", 3.0))
    pause_max = _env_float("ICDEV_LLM_PAUSE_MAX", _cfg_float(rl, "pause_max_seconds", 5.0))
    if pause_min < 0:
        pause_min = 0.0
    if pause_max < pause_min:
        pause_max = pause_min
    return max_parallel, pause_min, pause_max


def _cfg_float(rl: dict, key: str, default: float) -> float:
    try:
        return float(rl.get(key, default))
    except (TypeError, ValueError):
        return default


# scope value -> lease backend. "" = in-process cap only (default).
#   file = host-wide OS file lock (one host); pg = cluster-wide PG advisory lock
#   (all hosts sharing the PostgreSQL).
_SCOPE_BACKEND = {
    "process": "",
    "": "",
    "global": "file",
    "host": "file",
    "cross-process": "file",
    "cross_process": "file",
    "cluster": "pg",
    "multi-host": "pg",
    "multihost": "pg",
    "db": "pg",
    "pg": "pg",
    "postgres": "pg",
}


def resolve_lease_config(config: dict | None) -> Tuple[str, str, Optional[float]]:
    """Resolve ``(lease_backend, lease_name, lease_timeout)`` for the shared cap.

    ``lease_backend`` from ``rate_limit.scope`` (env ``ICDEV_LLM_RATE_LIMIT_SCOPE``):
    ``""`` (process — in-process cap only, default), ``"file"``
    (``global``/``host`` — one host, OS file lock), or ``"pg"``
    (``cluster``/``multi-host`` — all hosts sharing the PostgreSQL, advisory
    lock). Unknown scopes fall back to ``""``. ``lease_timeout`` of ``None`` waits
    indefinitely (safe — both lock types auto-release when a holder dies). Env
    wins over *config*.
    """
    rl = {}
    if isinstance(config, dict):
        rl = config.get("rate_limit") or {}

    scope = os.environ.get("ICDEV_LLM_RATE_LIMIT_SCOPE", "").strip().lower()
    if not scope:
        scope = str(rl.get("scope", "process")).strip().lower()
    backend = _SCOPE_BACKEND.get(scope, "")

    name = os.environ.get("ICDEV_LLM_LEASE_NAME", "").strip() or str(rl.get("lease_name", "llm")).strip() or "llm"

    timeout: Optional[float] = None
    raw = os.environ.get("ICDEV_LLM_LEASE_TIMEOUT", "").strip()
    if raw:
        try:
            timeout = float(raw)
        except ValueError:
            timeout = None
    else:
        val = rl.get("lease_timeout_seconds", None)
        if val not in (None, "", "null"):
            try:
                timeout = float(val)
            except (TypeError, ValueError):
                timeout = None
    if timeout is not None and timeout < 0:
        timeout = None
    return backend, name, timeout


def _acquire_lease(backend: str, lease_name: str, max_parallel: int, lease_timeout):
    """Acquire the shared lease for *backend* (``file`` or ``pg``), or None."""
    if backend == "file":
        return _cpl.acquire(lease_name, max_parallel, lease_timeout)
    if backend == "pg":
        return _pgl.acquire(lease_name, max_parallel, lease_timeout)
    return None


@contextmanager
def rate_gate(
    max_parallel: int,
    pause_min: float,
    pause_max: float,
    *,
    lease_backend: str = "",
    lease_name: str = "llm",
    lease_timeout: Optional[float] = None,
):
    """Serialize + throttle an LLM provider call when rate-limited mode is on.

    Acquires a global semaphore sized to *max_parallel* for the duration of the
    call, then sleeps a randomized ``uniform(pause_min, pause_max)`` **while
    still holding the slot** so the NEXT call cannot begin until the gap has
    elapsed. This guarantees consecutive (serialized) calls are at least
    *pause_min* seconds apart.

    When *lease_backend* is set, ALSO holds a shared lease sized to
    *max_parallel* so the cap spans beyond this process: ``"file"`` = host-wide
    OS file lock (:mod:`tools.llm.cross_process_lease`), ``"pg"`` = cluster-wide
    PostgreSQL advisory lock (:mod:`tools.llm.pg_lease`). The lease is held
    across the call AND the pause, so the inter-call gap is enforced cluster-wide
    too. A lease that can't be acquired (timeout / lock failure / DB down) is
    ``None`` and the call proceeds under the in-process cap alone (fail-open,
    never hangs).

    A no-op (yields immediately, zero overhead) when ``max_parallel <= 0``.
    """
    if max_parallel <= 0:
        yield
        return

    global _GATE, _GATE_LIMIT
    with _GATE_LOCK:
        # Rebuild if the configured limit changed at runtime (e.g. env toggled).
        if _GATE is None or _GATE_LIMIT != max_parallel:
            _GATE = threading.BoundedSemaphore(max_parallel)
            _GATE_LIMIT = max_parallel
        sem = _GATE  # capture: release must target the same object we acquired

    sem.acquire()
    lease = None
    try:
        if lease_backend:
            lease = _acquire_lease(lease_backend, lease_name, max_parallel, lease_timeout)
        yield
    finally:
        # Pause while STILL holding both the in-process slot and the shared
        # (host- or cluster-wide) lease, so the next caller — this process,
        # another process, or another host — can't start until the gap elapses.
        if pause_max > 0:
            time.sleep(random.uniform(pause_min, pause_max))
        if lease is not None:
            lease.release()
        sem.release()


def _reset_for_tests() -> None:
    """Drop the shared gate so tests start from a clean state."""
    global _GATE, _GATE_LIMIT
    with _GATE_LOCK:
        _GATE = None
        _GATE_LIMIT = None
