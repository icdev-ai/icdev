"""Rate-limited LLM mode: concurrency gate + randomized inter-call pause."""
import threading
import time

import pytest

from tools.llm import rate_gate
from tools.llm.rate_gate import rate_gate as gate
from tools.llm.rate_gate import resolve_lease_config, resolve_rate_limit


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in (
        "ICDEV_LLM_RATE_LIMIT",
        "ICDEV_LLM_MAX_PARALLEL",
        "ICDEV_LLM_PAUSE_MIN",
        "ICDEV_LLM_PAUSE_MAX",
        "ICDEV_LLM_RATE_LIMIT_SCOPE",
        "ICDEV_LLM_LEASE_NAME",
        "ICDEV_LLM_LEASE_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    rate_gate._reset_for_tests()
    yield
    rate_gate._reset_for_tests()


def test_lease_config_process_by_default():
    assert resolve_lease_config({}) == ("", "llm", None)
    assert resolve_lease_config({"rate_limit": {"scope": "process"}}) == ("", "llm", None)
    # unknown scope falls back to in-process
    assert resolve_lease_config({"rate_limit": {"scope": "bogus"}}) == ("", "llm", None)


def test_lease_config_global_maps_to_file():
    backend, name, timeout = resolve_lease_config(
        {"rate_limit": {"scope": "global", "lease_name": "k", "lease_timeout_seconds": 30}}
    )
    assert backend == "file" and name == "k" and timeout == 30.0
    assert resolve_lease_config({"rate_limit": {"scope": "host"}})[0] == "file"


def test_lease_config_cluster_maps_to_pg():
    assert resolve_lease_config({"rate_limit": {"scope": "cluster"}})[0] == "pg"
    assert resolve_lease_config({"rate_limit": {"scope": "multi-host"}})[0] == "pg"


def test_lease_config_env_overrides(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_RATE_LIMIT_SCOPE", "cluster")
    monkeypatch.setenv("ICDEV_LLM_LEASE_NAME", "envkey")
    monkeypatch.setenv("ICDEV_LLM_LEASE_TIMEOUT", "12.5")
    assert resolve_lease_config({"rate_limit": {"scope": "process"}}) == ("pg", "envkey", 12.5)


def test_gate_file_backend_acquires_and_releases_lease(monkeypatch):
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: None)
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 0.0)
    calls = {}

    class _FakeLease:
        def release(self):
            calls["released"] = True

    def _fake_acquire(name, max_slots, timeout):
        calls["acquire"] = (name, max_slots, timeout)
        return _FakeLease()

    monkeypatch.setattr(rate_gate._cpl, "acquire", _fake_acquire)
    with gate(1, 3.0, 5.0, lease_backend="file", lease_name="k", lease_timeout=9.0):
        pass
    assert calls["acquire"] == ("k", 1, 9.0)
    assert calls.get("released") is True


def test_gate_pg_backend_routes_to_pg_lease(monkeypatch):
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: None)
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 0.0)
    calls = {}

    class _FakeLease:
        def release(self):
            calls["released"] = True

    def _pg_acquire(name, max_slots, timeout):
        calls["pg"] = (name, max_slots, timeout)
        return _FakeLease()

    def _file_acquire(*a, **k):
        raise AssertionError("file backend used for a pg scope")

    monkeypatch.setattr(rate_gate._pgl, "acquire", _pg_acquire)
    monkeypatch.setattr(rate_gate._cpl, "acquire", _file_acquire)
    with gate(1, 3.0, 5.0, lease_backend="pg", lease_name="cluster-key", lease_timeout=7.0):
        pass
    assert calls["pg"] == ("cluster-key", 1, 7.0)
    assert calls.get("released") is True


def test_gate_failopen_when_lease_none(monkeypatch):
    # Lease unavailable (DB down / lock error) -> None -> call still proceeds.
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: None)
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(rate_gate._pgl, "acquire", lambda *a, **k: None)
    ran = []
    with gate(1, 3.0, 5.0, lease_backend="pg"):
        ran.append(True)
    assert ran == [True]


def test_gate_no_lease_when_backend_empty(monkeypatch):
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: None)
    called = []
    monkeypatch.setattr(rate_gate._cpl, "acquire", lambda *a, **k: called.append("file"))
    monkeypatch.setattr(rate_gate._pgl, "acquire", lambda *a, **k: called.append("pg"))
    with gate(1, 3.0, 5.0, lease_backend=""):
        pass
    assert called == []  # in-process only — no lease backend touched


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
