#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Resilience — Circuit Breaker.

D146: Application-level circuit breaker using ABC + in-memory state (stdlib only).
Three-state machine: CLOSED -> OPEN -> HALF_OPEN.

Follows the D66 provider pattern (ABC + implementations) used by rate_limiter.py.

Usage:
    from tools.resilience.circuit_breaker import get_circuit_breaker

    cb = get_circuit_breaker("bedrock")

    # Manual usage
    if cb.allow_request():
        try:
            result = call_bedrock()
            cb.record_success()
        except Exception as e:
            cb.record_failure()
            raise

    # Decorator usage
    from tools.resilience.circuit_breaker import circuit_breaker

    @circuit_breaker("bedrock")
    def call_bedrock():
        ...
"""

import abc
import functools
import logging
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Type

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.resilience.circuit_breaker")


# ---------------------------------------------------------------------------
# Circuit breaker states
# ---------------------------------------------------------------------------
class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation — requests flow through
    OPEN = "open"           # Tripped — requests are rejected
    HALF_OPEN = "half_open" # Testing — limited requests allowed


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG = {
    "failure_threshold": 5,
    "recovery_timeout_seconds": 30,
    "half_open_max_calls": 3,
}


def _load_config() -> dict:
    """Load circuit breaker config from args/resilience_config.yaml."""
    try:
        import yaml
        config_path = BASE_DIR / "args" / "resilience_config.yaml"
        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            return config.get("circuit_breaker", {})
    except Exception:
        pass
    return {}


def _get_service_config(service_name: str) -> dict:
    """Get circuit breaker config for a specific service."""
    config = _load_config()
    defaults = config.get("default", _DEFAULT_CONFIG)
    service_overrides = config.get("services", {}).get(service_name, {})
    merged = {**_DEFAULT_CONFIG, **defaults, **service_overrides}
    return merged


# ---------------------------------------------------------------------------
# ABC (D66 provider pattern)
# ---------------------------------------------------------------------------
class CircuitBreakerBackend(abc.ABC):
    """Abstract circuit breaker backend."""

    @abc.abstractmethod
    def allow_request(self) -> bool:
        """Check if a request is allowed through the circuit."""

    @abc.abstractmethod
    def record_success(self):
        """Record a successful call — resets failure count if HALF_OPEN."""

    @abc.abstractmethod
    def record_failure(self):
        """Record a failed call — may trip the circuit."""

    @abc.abstractmethod
    def get_state(self) -> CircuitState:
        """Get the current circuit state."""

    @abc.abstractmethod
    def reset(self):
        """Force reset to CLOSED state."""

    @abc.abstractmethod
    def get_stats(self) -> dict:
        """Return current stats (state, failures, last failure time, etc.)."""


# ---------------------------------------------------------------------------
# In-Memory Implementation (single-replica, default)
# ---------------------------------------------------------------------------
class InMemoryCircuitBreaker(CircuitBreakerBackend):
    """Thread-safe in-memory circuit breaker.

    State transitions:
        CLOSED -> OPEN:     failure_count >= failure_threshold
        OPEN -> HALF_OPEN:  recovery_timeout elapsed
        HALF_OPEN -> CLOSED: half_open_max_calls consecutive successes
        HALF_OPEN -> OPEN:   any failure
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 30,
        half_open_max_calls: int = 3,
        **kwargs,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout_seconds
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._last_state_change = time.time()
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_calls = 0
                    self._success_count = 0
                    return True
                return False

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            return False  # pragma: no cover

    def record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_max_calls:
                    self._transition_to(CircuitState.CLOSED)
                    self._failure_count = 0
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success in closed state
                self._failure_count = 0

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open trips back to open
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def get_state(self) -> CircuitState:
        with self._lock:
            # Check for automatic OPEN -> HALF_OPEN transition
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_calls = 0
                    self._success_count = 0
            return self._state

    def reset(self):
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "service": self.service_name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "half_open_calls": self._half_open_calls,
                "last_failure_time": self._last_failure_time,
                "last_state_change": self._last_state_change,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout,
            }

    def _transition_to(self, new_state: CircuitState):
        """Transition to a new state (must be called under lock)."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.time()
        if old_state != new_state:
            logger.info(
                "Circuit breaker '%s': %s -> %s",
                self.service_name,
                old_state.value,
                new_state.value,
            )


# ---------------------------------------------------------------------------
# Registry — singleton per service name
# ---------------------------------------------------------------------------
_registry: Dict[str, CircuitBreakerBackend] = {}
_registry_lock = threading.Lock()


def get_circuit_breaker(service_name: str) -> CircuitBreakerBackend:
    """Get or create a circuit breaker for a named service.

    Thread-safe singleton per service name. Configuration loaded from
    args/resilience_config.yaml with per-service overrides.
    """
    with _registry_lock:
        if service_name not in _registry:
            config = _get_service_config(service_name)
            _registry[service_name] = InMemoryCircuitBreaker(
                service_name=service_name,
                failure_threshold=config.get(
                    "failure_threshold", _DEFAULT_CONFIG["failure_threshold"]
                ),
                recovery_timeout_seconds=config.get(
                    "recovery_timeout_seconds",
                    _DEFAULT_CONFIG["recovery_timeout_seconds"],
                ),
                half_open_max_calls=config.get(
                    "half_open_max_calls", _DEFAULT_CONFIG["half_open_max_calls"]
                ),
            )
        return _registry[service_name]


def get_all_breakers() -> Dict[str, dict]:
    """Return stats for all registered circuit breakers."""
    with _registry_lock:
        return {name: cb.get_stats() for name, cb in _registry.items()}


def reset_all():
    """Reset all circuit breakers to CLOSED state."""
    with _registry_lock:
        for cb in _registry.values():
            cb.reset()


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------
def circuit_breaker(
    service_name: str,
    fallback: Optional[Callable] = None,
    raise_on_open: bool = True,
):
    """Decorator that wraps a function with circuit breaker protection.

    Args:
        service_name: Name of the service (e.g. "bedrock", "redis").
        fallback: Optional fallback function to call when circuit is OPEN.
        raise_on_open: If True and no fallback, raise ServiceUnavailableError.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cb = get_circuit_breaker(service_name)

            if not cb.allow_request():
                if fallback is not None:
                    logger.warning(
                        "Circuit breaker '%s' OPEN — calling fallback for %s",
                        service_name,
                        func.__name__,
                    )
                    return fallback(*args, **kwargs)
                if raise_on_open:
                    from tools.resilience.errors import ServiceUnavailableError

                    raise ServiceUnavailableError(
                        f"Circuit breaker '{service_name}' is OPEN",
                        service=service_name,
                    )
                return None

            try:
                result = func(*args, **kwargs)
                cb.record_success()
                return result
            except Exception as exc:
                cb.record_failure()
                raise

        return wrapper

    return decorator
