# CUI // SP-CTI
"""Tests for tools.resilience.circuit_breaker."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from tools.resilience.circuit_breaker import (
    CircuitState,
    InMemoryCircuitBreaker,
    circuit_breaker,
    get_all_breakers,
    get_circuit_breaker,
    reset_all,
    _registry,
    _registry_lock,
)
from tools.resilience.errors import ServiceUnavailableError


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------
SHORT_RECOVERY = 0.1  # seconds — keeps tests fast


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global circuit breaker registry before each test."""
    with _registry_lock:
        _registry.clear()
    yield
    with _registry_lock:
        _registry.clear()


def _make_cb(
    name: str = "test-svc",
    threshold: int = 3,
    recovery: float = SHORT_RECOVERY,
    half_open_max: int = 2,
) -> InMemoryCircuitBreaker:
    return InMemoryCircuitBreaker(
        service_name=name,
        failure_threshold=threshold,
        recovery_timeout_seconds=recovery,
        half_open_max_calls=half_open_max,
    )


# ---------------------------------------------------------------------------
# InMemoryCircuitBreaker — state machine
# ---------------------------------------------------------------------------
class TestInMemoryCircuitBreakerStates:
    """State-transition tests for InMemoryCircuitBreaker."""

    def test_initial_state_is_closed(self):
        cb = _make_cb()
        assert cb.get_state() == CircuitState.CLOSED

    def test_closed_to_open_after_threshold(self):
        cb = _make_cb(threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN

    def test_stays_closed_below_threshold(self):
        cb = _make_cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.get_state() == CircuitState.CLOSED

    def test_open_to_half_open_after_recovery_timeout(self):
        cb = _make_cb(threshold=1, recovery=SHORT_RECOVERY)
        cb.record_failure()  # trips to OPEN
        assert cb.get_state() == CircuitState.OPEN

        time.sleep(SHORT_RECOVERY + 0.05)
        assert cb.get_state() == CircuitState.HALF_OPEN

    def test_half_open_to_closed_after_successes(self):
        cb = _make_cb(threshold=1, recovery=SHORT_RECOVERY, half_open_max=2)
        cb.record_failure()  # -> OPEN
        time.sleep(SHORT_RECOVERY + 0.05)

        # allow_request transitions OPEN -> HALF_OPEN
        assert cb.allow_request() is True
        cb.record_success()
        cb.record_success()
        assert cb.get_state() == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self):
        cb = _make_cb(threshold=1, recovery=SHORT_RECOVERY, half_open_max=3)
        cb.record_failure()  # -> OPEN
        time.sleep(SHORT_RECOVERY + 0.05)

        assert cb.allow_request() is True  # -> HALF_OPEN
        cb.record_failure()  # any failure trips back to OPEN
        assert cb.get_state() == CircuitState.OPEN


# ---------------------------------------------------------------------------
# allow_request / record_success / reset / get_stats
# ---------------------------------------------------------------------------
class TestCircuitBreakerBehavior:
    """Behavioral tests for individual methods."""

    def test_allow_request_true_in_closed(self):
        cb = _make_cb()
        assert cb.allow_request() is True

    def test_allow_request_false_in_open_within_timeout(self):
        cb = _make_cb(threshold=1, recovery=10)  # long recovery
        cb.record_failure()  # -> OPEN
        assert cb.allow_request() is False

    def test_record_success_resets_failure_count_in_closed(self):
        cb = _make_cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        stats = cb.get_stats()
        assert stats["failure_count"] == 0

    def test_reset_forces_closed(self):
        cb = _make_cb(threshold=1)
        cb.record_failure()  # -> OPEN
        assert cb.get_state() == CircuitState.OPEN

        cb.reset()
        assert cb.get_state() == CircuitState.CLOSED
        stats = cb.get_stats()
        assert stats["failure_count"] == 0
        assert stats["success_count"] == 0
        assert stats["half_open_calls"] == 0

    def test_get_stats_returns_correct_dict(self):
        cb = _make_cb(name="my-svc", threshold=5, recovery=30, half_open_max=2)
        stats = cb.get_stats()
        assert stats["service"] == "my-svc"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["success_count"] == 0
        assert stats["failure_threshold"] == 5
        assert stats["recovery_timeout_seconds"] == 30


# ---------------------------------------------------------------------------
# Registry functions
# ---------------------------------------------------------------------------
class TestRegistry:
    """Tests for the global circuit breaker registry."""

    def test_get_circuit_breaker_returns_same_instance(self):
        cb1 = get_circuit_breaker("svc-a")
        cb2 = get_circuit_breaker("svc-a")
        assert cb1 is cb2

    def test_get_circuit_breaker_distinct_services(self):
        cb1 = get_circuit_breaker("svc-a")
        cb2 = get_circuit_breaker("svc-b")
        assert cb1 is not cb2

    def test_get_all_breakers(self):
        get_circuit_breaker("svc-x")
        get_circuit_breaker("svc-y")
        all_stats = get_all_breakers()
        assert "svc-x" in all_stats
        assert "svc-y" in all_stats
        assert all_stats["svc-x"]["state"] == "closed"

    def test_reset_all(self):
        cb = get_circuit_breaker("svc-reset")
        for _ in range(10):
            cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN

        reset_all()
        assert cb.get_state() == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# @circuit_breaker decorator
# ---------------------------------------------------------------------------
class TestCircuitBreakerDecorator:
    """Tests for the @circuit_breaker decorator."""

    def test_decorator_passes_through_on_closed(self):
        @circuit_breaker("dec-closed")
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_decorator_raises_service_unavailable_on_open(self):
        # Trip the breaker first
        cb = get_circuit_breaker("dec-open")
        for _ in range(cb.failure_threshold):
            cb.record_failure()

        @circuit_breaker("dec-open")
        def do_work():
            return "ok"

        with pytest.raises(ServiceUnavailableError):
            do_work()

    def test_decorator_calls_fallback_on_open(self):
        cb = get_circuit_breaker("dec-fb")
        for _ in range(cb.failure_threshold):
            cb.record_failure()

        def my_fallback(*args, **kwargs):
            return "fallback-result"

        @circuit_breaker("dec-fb", fallback=my_fallback)
        def do_work():
            return "ok"

        assert do_work() == "fallback-result"

    def test_decorator_records_failure_on_exception(self):
        @circuit_breaker("dec-exc")
        def fail():
            raise ValueError("boom")

        cb = get_circuit_breaker("dec-exc")
        with pytest.raises(ValueError):
            fail()

        stats = cb.get_stats()
        assert stats["failure_count"] == 1


# ---------------------------------------------------------------------------
# Config loading (mocked YAML)
# ---------------------------------------------------------------------------
class TestConfigLoading:
    """Test that config is loaded from YAML when available."""

    def test_config_loaded_from_yaml(self, tmp_path):
        yaml_content = {
            "circuit_breaker": {
                "default": {
                    "failure_threshold": 10,
                    "recovery_timeout_seconds": 60,
                    "half_open_max_calls": 5,
                },
                "services": {
                    "bedrock": {
                        "failure_threshold": 7,
                    }
                },
            }
        }
        config_file = tmp_path / "resilience_config.yaml"

        import yaml
        with open(str(config_file), "w") as f:
            yaml.dump(yaml_content, f)

        with patch(
            "tools.resilience.circuit_breaker.BASE_DIR", tmp_path.parent
        ):
            # Make config_path resolve to our tmp file
            args_dir = tmp_path / "args"
            args_dir.mkdir(exist_ok=True)
            real_config = args_dir / "resilience_config.yaml"
            with open(str(real_config), "w") as f:
                yaml.dump(yaml_content, f)

            with patch("tools.resilience.circuit_breaker.BASE_DIR", tmp_path):
                from tools.resilience.circuit_breaker import _get_service_config
                cfg = _get_service_config("bedrock")
                assert cfg["failure_threshold"] == 7
                # Default for non-overridden key
                assert cfg["recovery_timeout_seconds"] == 60


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------
class TestThreadSafety:
    """Verify thread-safe access to InMemoryCircuitBreaker."""

    def test_concurrent_failures(self):
        cb = _make_cb(name="thread-svc", threshold=50, recovery=SHORT_RECOVERY)
        barrier = threading.Barrier(10)

        def hammer():
            barrier.wait()
            for _ in range(10):
                cb.record_failure()

        threads = [threading.Thread(target=hammer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = cb.get_stats()
        # 10 threads x 10 failures = 100 total
        assert stats["failure_count"] == 100

    def test_concurrent_allow_request(self):
        cb = _make_cb(name="thread-allow", threshold=1000)
        results = []
        barrier = threading.Barrier(5)

        def check():
            barrier.wait()
            results.append(cb.allow_request())

        threads = [threading.Thread(target=check) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should be True in CLOSED state
        assert all(results)
        assert len(results) == 5
