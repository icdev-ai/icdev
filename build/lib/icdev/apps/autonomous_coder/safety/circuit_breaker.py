# CUI // SP-CTI
"""Circuit Breaker — prevents runaway agent loops and enforces token / step budgets."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class BreakerState(str, Enum):
    CLOSED = "closed"      # normal — requests flow through
    OPEN = "open"          # tripped — all requests rejected
    HALF_OPEN = "half_open"  # testing recovery


@dataclass
class BreakerConfig:
    max_steps: int = 20          # max agent iterations per session
    max_tokens: int = 32_000     # soft token budget
    max_duration_s: float = 300  # wall-clock cap — 300s for LLM pipelines (LL-002: 120s too low)
    max_failures: int = 3        # consecutive failures before trip


class CircuitBreakerTripped(RuntimeError):
    pass


class CircuitBreaker:
    """Stateful circuit breaker that wraps agent pipeline calls."""

    def __init__(self, config: BreakerConfig | None = None):
        self.cfg = config or BreakerConfig()
        self.state = BreakerState.CLOSED
        self._steps = 0
        self._tokens = 0
        self._failures = 0
        self._start: float = time.monotonic()
        self._abort_callbacks: list[Callable[[], None]] = []

    def on_abort(self, cb: Callable[[], None]) -> None:
        self._abort_callbacks.append(cb)

    def _trip(self, reason: str) -> None:
        self.state = BreakerState.OPEN
        for cb in self._abort_callbacks:
            try:
                cb()
            except Exception:
                pass
        raise CircuitBreakerTripped(f"Circuit breaker tripped: {reason}")

    def check(self) -> None:
        """Call before each agent step. Raises if any limit is exceeded."""
        if self.state == BreakerState.OPEN:
            raise CircuitBreakerTripped("Circuit is open — abort signal active")

        elapsed = time.monotonic() - self._start
        if elapsed > self.cfg.max_duration_s:
            self._trip(f"Duration exceeded {self.cfg.max_duration_s}s (was {elapsed:.1f}s)")

        if self._steps >= self.cfg.max_steps:
            self._trip(f"Step limit reached ({self._steps}/{self.cfg.max_steps})")

        if self._tokens >= self.cfg.max_tokens:
            self._trip(f"Token budget exhausted ({self._tokens}/{self.cfg.max_tokens})")

        if self._failures >= self.cfg.max_failures:
            self._trip(f"Too many consecutive failures ({self._failures})")

    def record_step(self, tokens_used: int = 0) -> None:
        self._steps += 1
        self._tokens += tokens_used
        self._failures = 0  # reset on success

    def record_failure(self) -> None:
        self._failures += 1

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "steps": self._steps,
            "tokens": self._tokens,
            "failures": self._failures,
            "elapsed_s": round(time.monotonic() - self._start, 2),
        }
