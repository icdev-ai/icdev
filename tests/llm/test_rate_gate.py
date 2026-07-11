"""Rate-limited LLM mode: concurrency gate + randomized inter-call pause."""
import threading
import time

import pytest

from tools.llm import rate_gate
from tools.llm.rate_gate import rate_gate as gate
from tools.llm.rate_gate import resolve_rate_limit


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in (
        "ICDEV_LLM_RATE_LIMIT",
        "ICDEV_LLM_MAX_PARALLEL",
        "ICDEV_LLM_PAUSE_MIN",
        "ICDEV_LLM_PAUSE_MAX",
    ):
        monkeypatch.delenv(var, raising=False)
    rate_gate._reset_for_tests()
    yield
    rate_gate._reset_for_tests()


def test_disabled_by_default_and_no_op(monkeypatch):
    assert resolve_rate_limit({}) == (0, 0.0, 0.0)
    assert resolve_rate_limit({"rate_limit": {"enabled": False}}) == (0, 0.0, 0.0)
    # max_parallel<=0 => gate yields immediately, never sleeps.
    slept = []
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: slept.append(s))
    with gate(0, 3.0, 5.0):
        pass
    assert slept == []


def test_config_enabled_defaults():
    mp, pmin, pmax = resolve_rate_limit({"rate_limit": {"enabled": True}})
    assert mp == 1 and pmin == 3.0 and pmax == 5.0


def test_env_overrides_win(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_RATE_LIMIT", "true")
    monkeypatch.setenv("ICDEV_LLM_MAX_PARALLEL", "2")
    monkeypatch.setenv("ICDEV_LLM_PAUSE_MIN", "1.5")
    monkeypatch.setenv("ICDEV_LLM_PAUSE_MAX", "4")
    assert resolve_rate_limit({"rate_limit": {"enabled": False, "max_parallel": 9}}) == (2, 1.5, 4.0)
    # explicit off env beats config on
    monkeypatch.setenv("ICDEV_LLM_RATE_LIMIT", "off")
    assert resolve_rate_limit({"rate_limit": {"enabled": True}}) == (0, 0.0, 0.0)


def test_pause_bounds_sanitized():
    # pause_max below pause_min is clamped up to pause_min
    mp, pmin, pmax = resolve_rate_limit(
        {"rate_limit": {"enabled": True, "pause_min_seconds": 5, "pause_max_seconds": 2}}
    )
    assert pmin == 5.0 and pmax == 5.0


def test_pause_uses_randomized_value_in_range(monkeypatch):
    calls = {}
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: calls.setdefault("range", (a, b)) or 0.0)
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: calls.setdefault("slept", True))
    with gate(1, 3.0, 5.0):
        pass
    assert calls["range"] == (3.0, 5.0)
    assert calls.get("slept") is True


def test_max_parallel_one_serializes(monkeypatch):
    # No real pause — keep the test fast.
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: None)
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 0.0)

    in_flight = 0
    max_seen = 0
    lock = threading.Lock()

    def worker():
        nonlocal in_flight, max_seen
        with gate(1, 3.0, 5.0):
            with lock:
                in_flight += 1
                max_seen = max(max_seen, in_flight)
            time.sleep(0.02)  # real sleep to create overlap pressure (not the gate pause)
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_seen == 1  # never more than one call in flight


def test_max_parallel_two_allows_two(monkeypatch):
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: None)
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 0.0)

    in_flight = 0
    max_seen = 0
    lock = threading.Lock()
    started = threading.Barrier(2, timeout=5)

    def worker():
        nonlocal in_flight, max_seen
        with gate(2, 0.0, 0.0):
            with lock:
                in_flight += 1
                max_seen = max(max_seen, in_flight)
            try:
                started.wait()  # force both slots occupied simultaneously
            except threading.BrokenBarrierError:
                pass
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_seen == 2
