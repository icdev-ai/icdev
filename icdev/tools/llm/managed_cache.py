# [TEMPLATE: CUI // SP-CTI]
"""Managed-object prefix cache: create, reuse, expire — with the economics gated.

cch-prov-02. The ``managed_object`` level that ``cch-cap-01`` added to
:class:`~tools.llm.provider.PrefixCacheCapability` exists because Gemini's
explicit caching is not a flag on a request. It is a **stored object** —
``cachedContents`` — with its own identity, its own TTL, and its own lifecycle:
create it, reference it by handle while it lives, let it expire. Neither of the
other two shapes fits: there is no breakpoint to mark (Anthropic's ``explicit``)
and there is no number to simply read back (OpenAI's ``automatic``).

This module owns that lifecycle, and it owns the decision of whether to enter it
at all — which is the harder half.

Why the gate is the point
-------------------------
A stored cache is billed **per token per hour of storage**, whether or not
anything reads it. So an object created for a prefix that is used once is
strictly worse than no caching: it pays the creation charge, pays rent, and
saves nothing. That failure mode is silent — the object is created, the call
succeeds, and the only trace is on the invoice.

Two runtime gates keep it out of that state, and they are MEASURED per prefix
rather than declared per surface:

* **Size.** ``min_prefix_tokens`` (default 4096). Measured on this platform
  (``docs/research/prefix-caching-assessment.md`` §3), 95.5% of calls are below
  even the 1024-token floor at which any vendor's prefix cache can fire.
* **Repetition.** ``min_sightings`` (default 2). The object is NOT created the
  first time a prefix is seen. A fingerprint must be seen again inside the TTL
  window before anything is stored, so "seen once" — the case that only ever
  loses money — never creates an object.

The break-even, stated so it can be re-derived when vendor prices move: a cached
read costs ~0.25x normal input, so each read saves ~0.75x. Storage costs S per
token-hour. Over a TTL of h hours with R reads, storing pays off when
``(R - 1) > S * h / (0.75 * P)``. At Gemini 2.5 Flash's published order of
magnitude (P ~ $0.30/1M input, S ~ $1.00/1M/hour) that is ``(R - 1) > 4.4 * h``:
**~6 reads inside an hour, but only 2 inside five minutes**. Hence the default
``ttl_seconds: 300`` paired with ``min_sightings: 2`` — a short TTL is what makes
a two-read prefix profitable. Prices are terms that change; re-check them before
acting on the arithmetic (assessment §5).

What this module does NOT do
----------------------------
It stores nothing durably. The registry is per-process, so a second worker
builds its own object for the same prefix and a process that dies leaves an
object renting until its TTL expires. Both costs are bounded BY the short TTL,
which is the other reason the default is 300s rather than an hour. A durable
cross-process registry is a separate question and needs a table; it is not
needed to make the lifecycle correct.

Nothing here is Gemini-specific: the handle is opaque, and the module never
imports a vendor SDK. The adapter that owns a ``managed_object`` capability
supplies the create/expire calls. Keeping the vendor handle out of
:class:`~tools.llm.provider.LLMRequest` is deliberate and is the same rule
cch-cap-01 established — a vendor field on a neutral request forces every other
vendor to recognise or ignore a foreign vocabulary.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
#: The feature is switched off. The default, everywhere.
CACHE_DISABLED = "disabled"
#: The stable prefix is too small for a stored object to be worth its rent.
CACHE_TOO_SMALL = "prefix_below_threshold"
#: Seen for the first time inside the window. Deliberately NOT cached — a prefix
#: used once costs more stored than not stored.
CACHE_FIRST_SIGHTING = "first_sighting"
#: Seen enough times, large enough, and no live object yet: create one.
CACHE_CREATE = "create"
#: A live object already covers this prefix: reference it.
CACHE_REUSE = "reuse"
#: The per-process object budget is full. Not an error — an explicit refusal, so
#: "we chose not to" never reads the same as "it did not fire".
CACHE_AT_CAPACITY = "at_capacity"
#: Creating an object for this prefix failed recently; suppressed until the
#: cooldown lapses so a failing prefix cannot storm the vendor on every call.
CACHE_SUPPRESSED = "create_failed_cooldown"

CACHE_ACTIONS = frozenset(
    {
        CACHE_DISABLED,
        CACHE_TOO_SMALL,
        CACHE_FIRST_SIGHTING,
        CACHE_CREATE,
        CACHE_REUSE,
        CACHE_AT_CAPACITY,
        CACHE_SUPPRESSED,
    }
)


@dataclass(frozen=True)
class ManagedCacheDecision:
    """What to do about this call's prefix, and why.

    Seven distinct actions rather than a bool, for the reason this codebase
    keeps re-learning: a zero has several causes and only some are defects.
    "Caching is off", "the prefix is 200 tokens", "we have seen it once" and
    "the vendor refused to create the object" all produce no cached tokens, and
    each sends you somewhere different.

    Attributes:
        action: One of :data:`CACHE_ACTIONS`.
        reason: Human-readable, carrying the measured numbers where relevant.
        fingerprint: Stable id of the prefix, or "" when nothing was computed.
        estimated_tokens: Estimated size of the stable prefix.
        handle: The provider's cache object, set only when action is ``reuse``.
        sightings: How many times this fingerprint has been seen in-window.
    """

    action: str
    reason: str
    fingerprint: str = ""
    estimated_tokens: int = 0
    handle: Any = None
    sightings: int = 0

    @property
    def uses_object(self) -> bool:
        """True when the call should go through a cache object (new or existing)."""
        return self.action in (CACHE_CREATE, CACHE_REUSE)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ManagedCacheConfig:
    """Economics knobs for the managed-object cache. Default OFF.

    Attributes:
        enabled: Master switch. False by default and everywhere — a stored cache
            is a standing cost, so it is opted into per deployment, never
            inherited.
        ttl_seconds: Lifetime requested from the vendor, and the width of the
            repetition window. Short on purpose: it bounds both the rent on an
            unused object and the orphan a dying process leaves behind.
        min_prefix_tokens: Below this estimate nothing is stored. Must stay at
            or above the vendor's own floor for the model in use (1024 tokens
            for Gemini 2.5 Flash, 2048 for 2.5 Pro at time of writing) — the
            floor is where the API *accepts* the object, not where it pays.
        min_sightings: How many times a fingerprint must appear inside the
            window before an object is created. 1 would cache single-use
            prefixes, which is the one case guaranteed to lose money.
        max_objects: Ceiling on live objects held per process. Each one is
            renting.
        chars_per_token: Divisor for the size estimate. A local heuristic on
            purpose — the vendor's exact count needs a network round trip, and
            paying for a call to decide whether to pay for a cache is backwards.
        failure_cooldown_seconds: How long a fingerprint is suppressed after a
            failed create.
    """

    enabled: bool = False
    ttl_seconds: int = 300
    min_prefix_tokens: int = 4096
    min_sightings: int = 2
    max_objects: int = 32
    chars_per_token: float = 4.0
    failure_cooldown_seconds: int = 600

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "ManagedCacheConfig":
        """Build from the ``managed_object_cache:`` block of llm_config.yaml.

        Absent block, absent keys and unusable values all fall back to the
        defaults — the failure mode of a malformed config must be "off", never
        "cache everything".
        """
        block = (config or {}).get("managed_object_cache") or {}
        if not isinstance(block, dict):
            return cls()

        defaults = cls()

        def _int(key: str, default: int, minimum: int) -> int:
            try:
                value = int(block.get(key, default))
            except (TypeError, ValueError):
                return default
            return value if value >= minimum else default

        def _float(key: str, default: float, minimum: float) -> float:
            try:
                value = float(block.get(key, default))
            except (TypeError, ValueError):
                return default
            return value if value >= minimum else default

        return cls(
            enabled=bool(block.get("enabled", defaults.enabled)),
            ttl_seconds=_int("ttl_seconds", defaults.ttl_seconds, 1),
            min_prefix_tokens=_int("min_prefix_tokens", defaults.min_prefix_tokens, 1),
            min_sightings=_int("min_sightings", defaults.min_sightings, 1),
            max_objects=_int("max_objects", defaults.max_objects, 1),
            chars_per_token=_float("chars_per_token", defaults.chars_per_token, 0.1),
            failure_cooldown_seconds=_int(
                "failure_cooldown_seconds", defaults.failure_cooldown_seconds, 1
            ),
        )


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
@dataclass
class _LiveObject:
    """A cache object believed to be alive at the vendor."""

    handle: Any
    created_at: float
    expires_at: float
    token_count: int = 0
    reads: int = 0


@dataclass
class _Sighting:
    count: int = 0
    expires_at: float = 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ManagedPrefixCache:
    """Per-process registry of managed cache objects, keyed by prefix fingerprint.

    Thread-safe: the router invokes providers from pool workers, so two threads
    can reach :meth:`decide` for the same prefix at once. The lock keeps the
    sighting counter and the object table consistent; it is not held across the
    vendor call that creates the object, so a duplicate create is possible under
    a race. :meth:`record_object` resolves that by keeping the first object
    registered and telling the caller to release the loser, rather than silently
    leaking one to rent until its TTL.
    """

    def __init__(self, config: Optional[ManagedCacheConfig] = None):
        self._config = config or ManagedCacheConfig()
        self._lock = threading.RLock()
        self._objects: Dict[str, _LiveObject] = {}
        self._sightings: Dict[str, _Sighting] = {}
        self._suppressed: Dict[str, float] = {}
        self._counters: Dict[str, int] = {}

    # -- introspection ----------------------------------------------------
    @property
    def config(self) -> ManagedCacheConfig:
        return self._config

    def stats(self) -> Dict[str, Any]:
        """Snapshot for logging and tests. Never raises."""
        with self._lock:
            self._purge_expired_locked(time.monotonic())
            return {
                "enabled": self._config.enabled,
                "live_objects": len(self._objects),
                "tracked_prefixes": len(self._sightings),
                "suppressed": len(self._suppressed),
                "ttl_seconds": self._config.ttl_seconds,
                "min_prefix_tokens": self._config.min_prefix_tokens,
                "min_sightings": self._config.min_sightings,
                "actions": dict(self._counters),
            }

    # -- fingerprinting ---------------------------------------------------
    def fingerprint(self, model_id: str, *parts: str) -> str:
        """Stable id for a prefix on a specific model.

        The model is part of the key because a cache object belongs to exactly
        one model at the vendor; the same system prompt served by two models
        needs two objects. Parts are NUL-joined so ``("ab", "c")`` and
        ``("a", "bc")`` cannot collide.
        """
        payload = "\x00".join([model_id or "", *(p or "" for p in parts)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def estimate_tokens(self, *parts: str) -> int:
        """Estimate the token count of a prefix from its character length."""
        chars = sum(len(p or "") for p in parts)
        return int(chars / self._config.chars_per_token)

    # -- the decision -----------------------------------------------------
    def decide(self, model_id: str, *parts: str) -> ManagedCacheDecision:
        """Decide what to do about this prefix, recording the sighting.

        Calling this IS the sighting — the repetition gate counts calls that
        reached the decision point, not calls that happened to succeed.
        """
        if not self._config.enabled:
            return self._record(
                ManagedCacheDecision(
                    action=CACHE_DISABLED,
                    reason=(
                        "managed_object_cache.enabled is false; a stored cache is a "
                        "standing per-hour cost and is opted into per deployment."
                    ),
                )
            )

        estimated = self.estimate_tokens(*parts)
        if estimated < self._config.min_prefix_tokens:
            return self._record(
                ManagedCacheDecision(
                    action=CACHE_TOO_SMALL,
                    reason=(
                        f"stable prefix ~{estimated} tokens is below the "
                        f"{self._config.min_prefix_tokens}-token floor; storing it "
                        "would cost rent to save nothing."
                    ),
                    estimated_tokens=estimated,
                )
            )

        fp = self.fingerprint(model_id, *parts)
        now = time.monotonic()

        with self._lock:
            self._purge_expired_locked(now)

            live = self._objects.get(fp)
            if live is not None:
                live.reads += 1
                return self._record(
                    ManagedCacheDecision(
                        action=CACHE_REUSE,
                        reason=(
                            f"live cache object, {int(live.expires_at - now)}s of TTL "
                            f"left, read #{live.reads}."
                        ),
                        fingerprint=fp,
                        estimated_tokens=estimated,
                        handle=live.handle,
                        sightings=self._sightings.get(fp, _Sighting()).count,
                    )
                )

            cooldown = self._suppressed.get(fp)
            if cooldown is not None:
                return self._record(
                    ManagedCacheDecision(
                        action=CACHE_SUPPRESSED,
                        reason=(
                            f"creating an object for this prefix failed recently; "
                            f"suppressed for another {int(cooldown - now)}s rather "
                            "than retrying on every call."
                        ),
                        fingerprint=fp,
                        estimated_tokens=estimated,
                    )
                )

            seen = self._sightings.get(fp)
            if seen is None:
                seen = _Sighting()
                self._sightings[fp] = seen
            seen.count += 1
            seen.expires_at = now + self._config.ttl_seconds

            if seen.count < self._config.min_sightings:
                return self._record(
                    ManagedCacheDecision(
                        action=CACHE_FIRST_SIGHTING,
                        reason=(
                            f"prefix seen {seen.count}/{self._config.min_sightings} "
                            f"times inside the {self._config.ttl_seconds}s window; a "
                            "prefix used once costs more stored than not stored."
                        ),
                        fingerprint=fp,
                        estimated_tokens=estimated,
                        sightings=seen.count,
                    )
                )

            if len(self._objects) >= self._config.max_objects:
                return self._record(
                    ManagedCacheDecision(
                        action=CACHE_AT_CAPACITY,
                        reason=(
                            f"{len(self._objects)} live objects already at the "
                            f"max_objects={self._config.max_objects} ceiling; each one "
                            "is renting, so this prefix waits."
                        ),
                        fingerprint=fp,
                        estimated_tokens=estimated,
                        sightings=seen.count,
                    )
                )

            return self._record(
                ManagedCacheDecision(
                    action=CACHE_CREATE,
                    reason=(
                        f"prefix ~{estimated} tokens seen {seen.count} times inside "
                        f"the {self._config.ttl_seconds}s window; storing it pays for "
                        "itself from the next read."
                    ),
                    fingerprint=fp,
                    estimated_tokens=estimated,
                    sightings=seen.count,
                )
            )

    # -- lifecycle --------------------------------------------------------
    def record_object(self, fingerprint: str, handle: Any, token_count: int = 0) -> bool:
        """Register a freshly created object. Returns True if it was kept.

        False means another thread won the race and its object is already
        registered; the caller should release the one it just created rather
        than let it rent unreferenced.
        """
        if not fingerprint or handle is None:
            return False
        now = time.monotonic()
        with self._lock:
            self._purge_expired_locked(now)
            if fingerprint in self._objects:
                return False
            self._objects[fingerprint] = _LiveObject(
                handle=handle,
                created_at=now,
                expires_at=now + self._config.ttl_seconds,
                token_count=max(0, int(token_count or 0)),
            )
            self._suppressed.pop(fingerprint, None)
            return True

    def note_failure(self, fingerprint: str) -> None:
        """Suppress further create attempts for this prefix for the cooldown."""
        if not fingerprint:
            return
        with self._lock:
            self._suppressed[fingerprint] = (
                time.monotonic() + self._config.failure_cooldown_seconds
            )

    def drop(self, fingerprint: str) -> None:
        """Forget an object — the vendor rejected the handle, or it was released."""
        if not fingerprint:
            return
        with self._lock:
            self._objects.pop(fingerprint, None)

    def expired_handles(self) -> list:
        """Purge expired entries and return their handles, oldest first.

        Expiry at the vendor is authoritative — the object dies on its own TTL
        whether or not anyone calls this. Returning the handles lets an adapter
        delete them early if it wants to stop paying rent sooner.
        """
        now = time.monotonic()
        with self._lock:
            return self._purge_expired_locked(now)

    def clear(self) -> None:
        """Drop all state. For tests and for a provider reset."""
        with self._lock:
            self._objects.clear()
            self._sightings.clear()
            self._suppressed.clear()
            self._counters.clear()

    # -- internals --------------------------------------------------------
    def _purge_expired_locked(self, now: float) -> list:
        dead = [fp for fp, obj in self._objects.items() if obj.expires_at <= now]
        handles = [self._objects.pop(fp).handle for fp in dead]
        for fp in [fp for fp, s in self._sightings.items() if s.expires_at <= now]:
            self._sightings.pop(fp, None)
        for fp in [fp for fp, until in self._suppressed.items() if until <= now]:
            self._suppressed.pop(fp, None)
        return handles

    def _record(self, decision: ManagedCacheDecision) -> ManagedCacheDecision:
        with self._lock:
            self._counters[decision.action] = self._counters.get(decision.action, 0) + 1
        return decision


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_CONFIG_LOCK = threading.Lock()
_LOADED_CONFIG: Optional[ManagedCacheConfig] = None


def load_managed_cache_config(force: bool = False) -> ManagedCacheConfig:
    """Read ``managed_object_cache:`` from the resolved llm_config.yaml, once.

    Read through :mod:`tools.llm.config_path` so this answers from the same file
    the router does — two copies of llm_config.yaml disagreeing about whether a
    standing cost is switched on is exactly the drift that module exists to
    prevent. Any failure to read yields the default (disabled).
    """
    global _LOADED_CONFIG
    with _CONFIG_LOCK:
        if _LOADED_CONFIG is not None and not force:
            return _LOADED_CONFIG
        try:
            import yaml

            from tools.llm.config_path import resolve_llm_config_path

            with open(resolve_llm_config_path(), encoding="utf-8") as fh:
                _LOADED_CONFIG = ManagedCacheConfig.from_config(yaml.safe_load(fh) or {})
        except Exception:
            _LOADED_CONFIG = ManagedCacheConfig()
        return _LOADED_CONFIG
