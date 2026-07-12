# CUI // SP-CTI
"""Cortex response cache — in-process LRU + TTL over governed facade results.

Opt-in (``cache.enabled`` in args/cortex_config.yaml, default False). Security
model: the key folds tenant_id + classification + domain + air_gap so a cached
result NEVER crosses any of those boundaries, and only the FINAL governed
(post-redaction) result is stored — a hit therefore never bypasses egress
governance. A cache HIT still emits a ``cortex_audit`` row (``cache_hit=True``,
zero incremental cost) so the append-only NIST-AU trail and /cortex/metrics stay
complete.

Covers the operations in ``cache.operations`` (default complete/search/ask).
NOTE: ``ask`` caches LIVE analyst answers over changing DB state, so its TTL is
deliberately short — enable it knowingly.
"""
from __future__ import annotations

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
}
_DEFAULT_OPERATIONS = ("cortex.complete", "cortex.search", "cortex.ask")


def _cache_cfg() -> dict:
    try:
        from .config import load_cortex_config
        return load_cortex_config().get("cache") or {}
    except Exception:  # noqa: BLE001 — missing/unreadable config -> disabled
        return {}


def is_enabled() -> bool:
    return bool(_cache_cfg().get("enabled", False))


def cacheable(operation: str) -> bool:
    ops = _cache_cfg().get("operations")
    return operation in (tuple(ops) if ops else _DEFAULT_OPERATIONS)


def _ttl_for(operation: str) -> float:
    ttls = {**_DEFAULT_TTL, **(_cache_cfg().get("ttl_seconds") or {})}
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


def _get_cache() -> _TTLCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = _TTLCache(int(_cache_cfg().get("max_entries") or _DEFAULT_MAX_ENTRIES))
    return _cache


def reset() -> None:
    """Drop the process cache (tests / config reload)."""
    global _cache
    with _cache_lock:
        _cache = None


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


def get_by_key(key: str):
    return _get_cache().get(key)


def put_by_key(key: str, value: Any, operation: str) -> None:
    _get_cache().put(key, value, _ttl_for(operation))


def audit_hit(operation: str, ctx) -> None:
    """Emit a cortex_audit row for a cache hit (cache_hit=True, zero new cost).

    A pre-pipeline hit skips GovernancePipeline.wrap, so without this the
    append-only NIST-AU trail and /cortex/metrics would silently undercount
    served responses. Best-effort — a metrics write must never break serving.
    """
    try:
        from .db.init_db import record_audit
        record_audit({
            "operation": operation,
            "tenant_id": getattr(ctx, "tenant_id", "") or "default",
            "classification": getattr(ctx, "classification", "") or "CUI",
            "user_id": getattr(ctx, "user_id", "") or "",
            "session_id": getattr(ctx, "session_id", "") or "",
            "domain": getattr(ctx, "domain", "") or "",
            "outcomes": {"cache": "pass"},
            "blocked": False,
            "cache_hit": True,
            "cost_usd": 0.0,
            "latency_ms": 0,
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex cache: hit audit failed: %s", exc)
