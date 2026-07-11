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
regardless of how many callers exist. It does NOT span processes — each process
applies its own cap (a per-API-key limit shared across processes needs a
cross-process lease, which is out of scope here).
"""

from __future__ import annotations

import os
import random
import threading
import time
from contextlib import contextmanager
from typing import Tuple

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


@contextmanager
def rate_gate(max_parallel: int, pause_min: float, pause_max: float):
    """Serialize + throttle an LLM provider call when rate-limited mode is on.

    Acquires a global semaphore sized to *max_parallel* for the duration of the
    call, then sleeps a randomized ``uniform(pause_min, pause_max)`` **while
    still holding the slot** so the NEXT call cannot begin until the gap has
    elapsed. This guarantees consecutive (serialized) calls are at least
    *pause_min* seconds apart.

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
    try:
        yield
    finally:
        if pause_max > 0:
            time.sleep(random.uniform(pause_min, pause_max))
        sem.release()


def _reset_for_tests() -> None:
    """Drop the shared gate so tests start from a clean state."""
    global _GATE, _GATE_LIMIT
    with _GATE_LOCK:
        _GATE = None
        _GATE_LIMIT = None
