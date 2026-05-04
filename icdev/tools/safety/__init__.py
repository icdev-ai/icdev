#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Safety — Circuit Breaker.

Three-state machine: CLOSED → OPEN → HALF_OPEN.
Standalone implementation for icdev/tools/safety; delegates to the full
resilience backend (tools.resilience.circuit_breaker) when available,
but works with stdlib alone as a fallback.
"""

import threading
import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker with three states.

    State transitions:
        CLOSED    → OPEN:      failure_count reaches failure_threshold
        OPEN      → HALF_OPEN: recovery_timeout seconds elapsed
        HALF_OPEN → CLOSED:    success_threshold consecutive successes
        HALF_OPEN → OPEN:      any failure
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        success_threshold: int = 3,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout_seconds
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def is_request_allowed(self) -> bool:
        """Return True if the circuit allows a new request."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                return True
            return False  # OPEN

    def record_success(self) -> None:
        """Record a successful outcome; may close the circuit from HALF_OPEN."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._transition(CircuitState.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed outcome; may open or re-open the circuit."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._success_count = 0
                self._transition(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._transition(CircuitState.OPEN)

    def reset(self) -> None:
        """Force-reset to CLOSED state."""
        with self._lock:
            self._transition(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0

    def get_stats(self) -> dict:
        with self._lock:
            self._maybe_transition_to_half_open()
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout,
                "success_threshold": self.success_threshold,
                "last_failure_time": self._last_failure_time,
            }

    # ------------------------------------------------------------------
    # Internal helpers (must be called under lock)
    # ------------------------------------------------------------------

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._success_count = 0
                self._transition(CircuitState.HALF_OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        self._state = new_state
