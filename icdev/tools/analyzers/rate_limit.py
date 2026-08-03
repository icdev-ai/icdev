#!/usr/bin/env python3
# CUI // SP-CTI
"""Per-analyzer rate limiting (anz-rate-01).

External threat-intel APIs meter us: SAM.gov, CISA KEV, NVD, ACLED and the
OSINT feeds all publish a quota, and a fan-out that runs every analyzer
accepting an observable type is exactly the shape that burns one analyzer's
quota on behalf of all the others. TheHive's Cortex rate-limits per analyzer
for this reason. ``args/analyzer_contract.yaml`` already *declares* the limit
per analyzer (``rate_limit.max_calls`` / ``rate_limit.per_seconds``); this
module is where it is *enforced*.

EXCEEDING A LIMIT IS NEVER A DROP
--------------------------------
A limiter that returns "no" and leaves the caller to shrug is a silent
data-loss bug: the fan-out would report the same thing whether the analyzer
found nothing or was never asked. So :meth:`AnalyzerRateLimiter.acquire`
resolves to exactly one of two visible outcomes and never to a discard:

    queue     wait, bounded by ``max_wait_seconds``, for the window to free a
              slot — then run. ``decision.waited_seconds`` records the delay.
    report    return ``allowed=False`` with ``retry_after_seconds``, the exact
              wall-clock delay after which a slot exists. The dispatcher turns
              that into a ``rate_limited`` report, which sets
              ``DispatchResult.partial``.

Queueing is opt-in and defaults off, because these windows are hours wide: a
60-per-hour analyzer that is out of quota frees its next slot in minutes, and
blocking a pooled worker thread that long would starve the fan-out to protect
one analyzer's quota. Reporting with ``retry_after_seconds`` lets the caller
re-submit deliberately. Short waits (a burst against a per-minute window) are
what ``max_wait_seconds`` is for.

WHY NOT tools/saas/rate_limiter.py
----------------------------------
That limiter is a Flask ``before_request`` middleware keyed by tenant, and its
backends hardcode a two-window vocabulary (``per_minute`` / ``per_hour``) drawn
from the SaaS tier table. Analyzer limits are keyed by analyzer, use an
arbitrary ``per_seconds`` window declared per analyzer, and need the queue/report
distinction above, which an HTTP 429 middleware has no use for. Reusing it would
have meant widening a request-path hot spot to serve a different caller — so
this module stays separate and small, and the SaaS limiter is untouched.

Usage:
    >>> from tools.analyzers.rate_limit import get_limiter
    >>> decision = get_limiter().acquire("cve_triage", max_calls=240, per_seconds=3600)
    >>> decision.allowed
    True
"""

from __future__ import annotations

import collections
import dataclasses
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analyzer_rate_limit")

__all__ = [
    "AnalyzerRateLimiter",
    "RateLimitDecision",
    "get_limiter",
    "reset_limiter",
]

CLASSIFICATION = "CUI // SP-CTI"

#: Floor on the poll interval while queueing, so a limiter waiting on a slot
#: that is milliseconds away does not spin the CPU.
_MIN_SLEEP_SECONDS = 0.01


@dataclasses.dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of asking for one call slot.

    ``allowed`` is the only field a caller must branch on; the rest exist so
    that a refusal can be *reported* with enough detail to be acted on rather
    than merely counted.
    """

    analyzer: str
    allowed: bool
    max_calls: int
    per_seconds: int
    remaining: int
    #: Wall-clock seconds until a slot frees. ``0.0`` when ``allowed``.
    retry_after_seconds: float = 0.0
    #: How long :meth:`AnalyzerRateLimiter.acquire` queued before deciding.
    waited_seconds: float = 0.0

    @property
    def queued(self) -> bool:
        """True when this decision cost the caller a measurable wait."""
        return self.waited_seconds > 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analyzer": self.analyzer,
            "allowed": self.allowed,
            "max_calls": self.max_calls,
            "per_seconds": self.per_seconds,
            "remaining": self.remaining,
            "retry_after_seconds": round(self.retry_after_seconds, 4),
            "waited_seconds": round(self.waited_seconds, 4),
        }

    def detail(self) -> str:
        """Human-readable refusal reason, used verbatim in dispatch reports."""
        return (
            f"rate limit reached: {self.max_calls} call(s) per {self.per_seconds}s "
            f"for analyzer {self.analyzer!r}; retry after "
            f"{self.retry_after_seconds:.1f}s"
        )


class AnalyzerRateLimiter:
    """Sliding-window call limiter keyed by analyzer.

    A sliding window rather than a fixed one because a fixed window lets an
    analyzer spend its entire hourly quota in the two seconds spanning a
    boundary — twice the declared ceiling against an API that meters us. The
    window keeps at most ``max_calls`` timestamps per analyzer, so memory is
    bounded by the declaration itself.

    Thread-safe: the fan-out in :mod:`tools.analyzers.dispatch` calls this from
    pooled worker threads concurrently. The lock is held only for the O(1)
    prune-and-consume, never across a sleep.
    """

    def __init__(
        self,
        *,
        time_source: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Injectable clock/sleep so tests can prove windowing without spending
        # wall-clock seconds waiting for an hour-wide window to roll.
        self._time = time_source
        self._sleep = sleep
        self._calls: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    # -- internals ----------------------------------------------------------

    def _prune(self, key: str, per_seconds: int, now: float) -> Deque[float]:
        """Drop timestamps that have aged out of *key*'s window."""
        log = self._calls.get(key)
        if log is None:
            log = collections.deque()
            self._calls[key] = log
        cutoff = now - per_seconds
        while log and log[0] <= cutoff:
            log.popleft()
        return log

    def _try_consume(self, key: str, max_calls: int, per_seconds: int):
        """Consume a slot if one is free. Caller holds the lock."""
        now = self._time()
        log = self._prune(key, per_seconds, now)
        if len(log) < max_calls:
            log.append(now)
            return True, 0.0, max_calls - len(log)
        # The window frees a slot when its oldest call ages out.
        retry_after = max(0.0, log[0] + per_seconds - now)
        return False, retry_after, 0

    # -- API ----------------------------------------------------------------

    def acquire(
        self,
        analyzer: str,
        max_calls: int,
        per_seconds: int,
        *,
        max_wait_seconds: float = 0.0,
    ) -> RateLimitDecision:
        """Ask for one call slot for *analyzer*.

        Args:
            analyzer: Declaration key the limit is tracked against.
            max_calls: Slots per window, from the declaration.
            per_seconds: Window width in seconds, from the declaration.
            max_wait_seconds: Queue up to this long for a slot before
                reporting. ``0.0`` (the default) reports immediately.

        Returns:
            A :class:`RateLimitDecision`. ``allowed=True`` means a slot was
            consumed and the caller must proceed; ``allowed=False`` carries
            ``retry_after_seconds`` so the refusal can be reported rather than
            swallowed. This method never raises for an exhausted window.
        """
        # A non-positive ceiling would mean "never runnable", which no
        # declaration can express (the contract requires positive ints) and
        # which would deadlock a queueing caller. Treat it as unlimited and say
        # so, rather than silently blocking the analyzer forever.
        if max_calls <= 0 or per_seconds <= 0:
            logger.warning(
                "analyzer %s has a non-positive rate limit (%s/%ss); treating as "
                "unlimited — fix the declaration in args/analyzer_contract.yaml",
                analyzer,
                max_calls,
                per_seconds,
            )
            return RateLimitDecision(
                analyzer=analyzer,
                allowed=True,
                max_calls=max_calls,
                per_seconds=per_seconds,
                remaining=-1,
            )

        started = self._time()
        deadline = started + max(0.0, float(max_wait_seconds))

        while True:
            with self._lock:
                allowed, retry_after, remaining = self._try_consume(
                    analyzer, max_calls, per_seconds
                )
            waited = self._time() - started

            if allowed:
                if waited > 0.0:
                    logger.info(
                        "analyzer %s queued %.2fs for a rate-limit slot (%s/%ss)",
                        analyzer,
                        waited,
                        max_calls,
                        per_seconds,
                    )
                return RateLimitDecision(
                    analyzer=analyzer,
                    allowed=True,
                    max_calls=max_calls,
                    per_seconds=per_seconds,
                    remaining=remaining,
                    waited_seconds=waited,
                )

            now = self._time()
            if now >= deadline:
                logger.warning(
                    "analyzer %s is rate limited (%s/%ss); reported with "
                    "retry_after=%.1fs — not dropped",
                    analyzer,
                    max_calls,
                    per_seconds,
                    retry_after,
                )
                return RateLimitDecision(
                    analyzer=analyzer,
                    allowed=False,
                    max_calls=max_calls,
                    per_seconds=per_seconds,
                    remaining=0,
                    retry_after_seconds=retry_after,
                    waited_seconds=waited,
                )

            # Sleep the shorter of "until a slot exists" and "until the caller's
            # patience runs out", with a floor so this cannot become a spin.
            self._sleep(max(_MIN_SLEEP_SECONDS, min(retry_after, deadline - now)))

    def snapshot(self, analyzer: str, max_calls: int, per_seconds: int) -> Dict[str, Any]:
        """Current window state for *analyzer* without consuming a slot.

        For introspection surfaces (``analyzer_capabilities``, dashboards). It
        prunes, so the numbers are current rather than cumulative.
        """
        with self._lock:
            log = self._prune(analyzer, per_seconds, self._time())
            used = len(log)
            oldest = log[0] if log else None
            now = self._time()
        return {
            "analyzer": analyzer,
            "max_calls": max_calls,
            "per_seconds": per_seconds,
            "used": used,
            "remaining": max(0, max_calls - used),
            "retry_after_seconds": (
                round(max(0.0, oldest + per_seconds - now), 4)
                if oldest is not None and used >= max_calls
                else 0.0
            ),
        }

    def tracked(self) -> List[str]:
        """Analyzer keys with a live window. Test and diagnostic helper."""
        with self._lock:
            return sorted(self._calls)

    def reset(self, analyzer: Optional[str] = None) -> None:
        """Forget *analyzer*'s window, or every window when omitted."""
        with self._lock:
            if analyzer is None:
                self._calls.clear()
            else:
                self._calls.pop(analyzer, None)


# ---------------------------------------------------------------------------
# Process-wide limiter
# ---------------------------------------------------------------------------
# One limiter per process, for the same reason the dispatch pool is
# process-wide: a per-call limiter would reset the window on every dispatch and
# enforce nothing at all.

_LIMITER: Optional[AnalyzerRateLimiter] = None
_LIMITER_LOCK = threading.Lock()


def get_limiter() -> AnalyzerRateLimiter:
    """The process-wide analyzer rate limiter (lazily created, thread-safe)."""
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                _LIMITER = AnalyzerRateLimiter()
    return _LIMITER


def reset_limiter() -> None:
    """Clear every tracked window on the process-wide limiter (tests)."""
    get_limiter().reset()
