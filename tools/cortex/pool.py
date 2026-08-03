# CUI // SP-CTI
"""Bounded, process-wide worker pools for governed-LLM fan-out.

A governed Cortex call is expensive — every one pays the full 7-gate pipeline —
so a caller that issues N *independent* governed calls in a `for` loop pays N
round trips end to end. The fix is fan-out, and the fan-out shape this codebase
has already settled on is a **process-wide bounded pool that is never shut down
per call** (``search_service._get_search_executor``, reused again by
``analyzers/dispatch._get_executor``).

That shape matters for a reason worth restating, because the obvious
alternative is wrong: a fresh ``ThreadPoolExecutor`` per call, shut down with
``wait=False``, leaks a worker thread for every task that outlives its timeout —
Python cannot force-kill a thread, so under sustained slowness *every* call
spawns threads that never go away. A single bounded pool caps the total; a
straggler's thread simply returns to the pool when its (already time-limited)
work finally finishes.

This module exists so the shape stops being hand-copied. It lives under
``tools/cortex/`` because what it fans out is governed Cortex calls, and because
that root is mirrored to ``icdev/tools/cortex/`` so generated child apps inherit
it.

**Deadlock rule.** A function running inside pool ``X`` must never call
``map_ordered`` on pool ``X`` — the outer tasks would hold every worker while
waiting on inner tasks that can never be scheduled. Give each call site its own
named pool (that is what ``name`` is for) and the rule holds by construction.
"""
from __future__ import annotations

import concurrent.futures
import os
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Governed calls are rate-limited and audited upstream, so the default stays
# modest: enough to collapse a serial loop by several-fold without turning one
# document scan into a burst the provider throttles.
DEFAULT_MAX_WORKERS = 4

_POOLS: Dict[str, concurrent.futures.ThreadPoolExecutor] = {}
_POOLS_LOCK = threading.Lock()


def get_pool(
    name: str,
    *,
    max_workers: Optional[int] = None,
    env_var: Optional[str] = None,
    default_workers: int = DEFAULT_MAX_WORKERS,
) -> concurrent.futures.ThreadPoolExecutor:
    """Return the lazily-created, process-wide pool registered under ``name``.

    Thread-safe (double-checked lock). The pool is sized once, on first use,
    from ``max_workers`` else ``env_var`` else ``default_workers``; an
    unparseable value falls back to ``default_workers``. It is intentionally
    never shut down — it is shared across every call at that name.
    """
    pool = _POOLS.get(name)
    if pool is not None:
        return pool
    with _POOLS_LOCK:
        pool = _POOLS.get(name)
        if pool is None:
            raw = max_workers
            if raw is None and env_var:
                raw = os.environ.get(env_var)
            try:
                resolved = int(raw) if raw is not None else int(default_workers)
            except (TypeError, ValueError):
                resolved = int(default_workers)
            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, resolved),
                thread_name_prefix=f"cortex-{name}",
            )
            _POOLS[name] = pool
    return pool


def map_ordered(
    pool: concurrent.futures.ThreadPoolExecutor,
    fn: Callable[[Any], Any],
    items: Iterable[Any],
) -> List[Tuple[Any, Optional[BaseException]]]:
    """Run ``fn`` over ``items`` concurrently; return ``(result, exc)`` per item.

    Two guarantees, and they are the whole point of this helper:

    * **Order.** The returned list is in ITEM order, not completion order, so a
      parallelised loop produces byte-identical output to the serial one it
      replaced.
    * **Isolation.** One item that raises yields ``(None, exc)`` in its slot and
      costs the others nothing. The caller decides what a failure means — this
      function never swallows one silently.

    A single item is run inline: one governed call is not worth a thread hop,
    and it keeps the common small-input case (and its test stubs) single-
    threaded.
    """
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        try:
            return [(fn(items[0]), None)]
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            return [(None, exc)]

    futures = [pool.submit(fn, item) for item in items]
    out: List[Tuple[Any, Optional[BaseException]]] = []
    for fut in futures:
        try:
            out.append((fut.result(), None))
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            out.append((None, exc))
    return out
