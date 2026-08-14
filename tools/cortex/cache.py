# CUI // SP-CTI
"""Cortex response cache — in-process LRU + TTL over governed facade results.

Opt-in (``cache.enabled`` in args/cortex_config.yaml, default False). Security
model: the key folds tenant_id + classification + domain + air_gap so a cached
result NEVER crosses any of those boundaries, and only the FINAL governed
(post-redaction) result is stored — a hit therefore never bypasses egress
governance. A cache HIT still emits a ``cortex_audit`` row (``cache_hit=True``,
zero incremental cost) so the append-only NIST-AU trail and /cortex/metrics stay
complete.

Covers the operations in ``cache.operations`` (default complete/search/
classify/extract). ``classify`` and ``extract`` are the most deterministic and
highest-repeat of the five — pure functions of (text, labels|schema, function)
with no DB or corpus state behind them. ``classify`` DEGRADES to a
query_classifier heuristic when the router is unavailable and that result is not
blocked, so it is cacheable too; its shorter TTL bounds how long a transient
outage can pin a degraded label.

ENTRIES ARE COPIED IN AND OUT (ctx-perf-06). ``put_by_key`` stores a deepcopy and
``get_by_key`` returns one, so no caller — not the one that produced the entry,
not the tenth one served from it — ever holds the stored object. Handing out the
live instance would mean one caller doing ``result.text = summarize(result.text)``
or ``result.metadata["seen"] = True`` silently rewrites the answer every
subsequent hit serves, for the whole TTL, with nothing in the audit trail to show
it. ``metrics.py`` copies its memo for exactly this reason. The copy is also what
makes the miss path safe: the caller that populated the entry gets the original
object back and may mutate it freely.

WHY ``cortex.ask`` IS NOT CACHEABLE BY DEFAULT (ctx-perf-06). This module has no
invalidation beyond TTL expiry and LRU eviction, and for ``ask`` no sound one can
be built: it is live NL->SQL over the operational database, so its correct
invalidation trigger is "any write to any table the generated SQL touched" —
authored by every subsystem in the platform, and mostly by OTHER processes (the
kanban runner, reflex daemons, ingestion workers, an operator at psql). An
in-process cache cannot observe those writes at all, so a corpus-change hook here
would cover the minority of writes that happen to share this interpreter and
would advertise a freshness guarantee it cannot keep. A partial hook is worse
than none. The other four operations are cacheable because they are pure
functions of their arguments (complete/classify/extract) or read a corpus whose
mutations DO have an in-process choke point (search — see :func:`invalidate`,
called from tools/rag/ingestion_manager.py).

``cortex.ask`` keeps its 30s entry in ``ttl_seconds`` deliberately: an operator
who knowingly adds it back to ``operations`` gets the short bound rather than
silently inheriting ``default: 300``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_ENTRIES = 512
_DEFAULT_TTL = {
    "default": 300.0,
    "cortex.complete": 900.0,
    "cortex.search": 120.0,
    "cortex.ask": 30.0,
    "cortex.classify": 600.0,
    "cortex.extract": 900.0,
}
# NOTE: cortex.ask is deliberately absent — see the module docstring. It keeps a
# TTL above so re-adding it to `operations` is knowingly short-bounded.
_DEFAULT_OPERATIONS = (
    "cortex.complete", "cortex.search",
    "cortex.classify", "cortex.extract",
)


def _cache_cfg(config=None) -> dict:
    """The ``cache`` section. ``config`` is a caller's cortex-config snapshot.

    Every read below routes through here, so it stays the single seam tests
    patch to force a cache posture. ``config`` exists because the decision to
    consult the cache costs three reads of the same file otherwise —
    ``is_enabled`` then ``cacheable`` then ``_ttl_for`` — and the governed
    wrapper already holds the snapshot (ctx-perf-01).
    """
    try:
        if config is not None:
            return config.get("cache") or {}
        from .config import load_cortex_config
        return load_cortex_config().get("cache") or {}
    except Exception:  # noqa: BLE001 — missing/unreadable config -> disabled
        return {}


def is_enabled(config=None) -> bool:
    return bool(_cache_cfg(config).get("enabled", False))


def cacheable(operation: str, config=None) -> bool:
    ops = _cache_cfg(config).get("operations")
    return operation in (tuple(ops) if ops else _DEFAULT_OPERATIONS)


def _ttl_for(operation: str, config=None) -> float:
    ttls = {**_DEFAULT_TTL, **(_cache_cfg(config).get("ttl_seconds") or {})}
    return float(ttls.get(operation, ttls.get("default", 300.0)))


class _TTLCache:
    """Thread-safe LRU cache with per-entry TTL (monotonic clock)."""

    def __init__(self, max_entries: int):
        self._max = max(1, int(max_entries))
        self._d: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str):
        now = time.monotonic()
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            expiry, value = item
            if expiry < now:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)  # LRU touch
            return value

    def put(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._d[key] = (time.monotonic() + ttl, value)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)  # evict least-recently-used

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._d)


_cache: Optional[_TTLCache] = None
_cache_lock = threading.Lock()


def _get_cache(config=None) -> _TTLCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = _TTLCache(
                    int(_cache_cfg(config).get("max_entries") or _DEFAULT_MAX_ENTRIES)
                )
    return _cache


def reset() -> None:
    """Drop the process cache singleton (tests / config reload).

    Stronger than :func:`invalidate` — the next use rebuilds the cache and
    re-reads ``max_entries``, which is otherwise read ONCE at first use and so
    cannot be changed by editing config in a running process.
    """
    global _cache
    with _cache_lock:
        _cache = None


def invalidate(reason: str = "") -> int:
    """Purge every cached entry; returns how many were dropped.

    The explicit purge path TTL expiry alone does not give. Callers are the
    places that mutate state a cached answer was derived from AND run in this
    process — today the RAG corpus paths that back ``cortex.search``:
    ``ingestion_manager.ingest_source`` / ``ingest_single_record`` and
    ``mcp/rag_server.handle_rag_delete_source`` (deletion is the sharper case, a
    cached answer can keep citing a source that no longer exists). Scope is
    deliberately whole-cache rather than per-operation:
    the key is a digest, so there is no way to select the subset of entries a
    given corpus change touched, and over-purging costs a recompute while
    under-purging serves a stale answer.

    Bounded honestly: this cache is per-process and in-memory, so a purge here
    does not reach another worker's copy. That is exactly why ``cortex.ask`` —
    whose invalidating writes come overwhelmingly from other processes — is not
    in the default ``operations`` list at all (module docstring).
    """
    with _cache_lock:
        cache = _cache
    if cache is None:
        return 0
    dropped = len(cache)
    cache.clear()
    if dropped:
        logger.info("cortex cache: invalidated %d entries (%s)", dropped, reason or "unspecified")
    return dropped


def make_key(operation: str, text: str, ctx, extra: dict) -> str:
    """Cache key folding the FULL security boundary + every output-affecting arg.

    tenant_id + classification + domain + air_gap partition the cache so a hit
    can never cross those. user_id is intentionally excluded (same tenant +
    classification legitimately share an answer). ``extra`` is the facade's other
    bound args (system_prompt / function / max_tokens / temperature / labels /
    top_k / ...) so two calls that differ only in those never collide.
    """
    parts = {
        "op": operation,
        "text": text,
        "tenant": getattr(ctx, "tenant_id", "") or "",
        "classification": getattr(ctx, "classification", "") or "",
        "domain": getattr(ctx, "domain", "") or "",
        "air_gap": bool(getattr(ctx, "air_gap", False)),
        "extra": extra,
    }
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_by_key(key: str, config=None):
    """Return an INDEPENDENT copy of the cached value, or None on a miss.

    A copy failure is reported as a miss: re-running the operation is correct
    but slow, whereas returning the stored instance is fast and unsafe.
    """
    value = _get_cache(config).get(key)
    if value is None:
        return None
    try:
        return copy.deepcopy(value)
    except Exception as exc:  # noqa: BLE001 — uncopyable payload -> serve a miss
        logger.debug("cortex cache: hit not copyable, treating as miss: %s", exc)
        return None


def put_by_key(key: str, value: Any, operation: str, config=None) -> None:
    """Store an INDEPENDENT copy, so the producing caller cannot poison the entry.

    A copy failure means the entry is not stored at all — never stored by
    reference, which is the failure mode this whole indirection exists to
    prevent.
    """
    try:
        stored = copy.deepcopy(value)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex cache: result not copyable, not caching %s: %s", operation, exc)
        return
    _get_cache(config).put(key, stored, _ttl_for(operation, config))


def audit_hit(operation: str, ctx) -> None:
    """Emit a cortex_audit row for a cache hit (cache_hit=True, zero new cost).

    A pre-pipeline hit skips GovernancePipeline.wrap, so without this the
    append-only NIST-AU trail and /cortex/metrics would silently undercount
    served responses. Best-effort — a metrics write must never break serving.

    Routed through ``record_governed_call`` so a hit costs ONE connection, not
    one per write (cxo-perf-03) — otherwise the cache saves the LLM call but not
    the DB round-trip it was meant to make cheap.
    """
    try:
        from .db.init_db import record_governed_call
        record_governed_call({
            "operation": operation,
            "tenant_id": getattr(ctx, "tenant_id", "") or "default",
            "classification": getattr(ctx, "classification", "") or "CUI",
            "user_id": getattr(ctx, "user_id", "") or "",
            "session_id": getattr(ctx, "session_id", "") or "",
            "domain": getattr(ctx, "domain", "") or "",
            "air_gap": bool(getattr(ctx, "air_gap", False)),
            "outcomes": {"cache": "pass"},
            "blocked": False,
            "cache_hit": True,
            "cost_usd": 0.0,
            "latency_ms": 0,
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex cache: hit audit failed: %s", exc)
