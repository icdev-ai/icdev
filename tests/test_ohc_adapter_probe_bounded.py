# CUI // SP-CTI
"""qa-fail-c614b0be02b65e98: the /ops adapter probe is concurrent and bounded.

`GET /ops` is the only ops route that health-probes every adapter during page
render.  `probe_all` ran those probes SERIALLY with no deadline, so the page cost
the SUM of 11 network round-trips -- and the round-trips that cost the most are
the ones to adapters that are ABSENT, because an absent service is a connection
timeout rather than a refusal.  Measured on the live dashboard 2026-08-25: 8.6s
cold for `GET /ops` against 0.25-0.46s for every sibling `/ops/*` route, of which
9.3s of 11.34s isolated was two absent adapters (`sagemaker` 5.17s on a rejected
AWS token, `prometheus` 4.14s on an unreachable localhost:9090).  Playwright
allows 10s, so the page timed out intermittently -- the E2E failure this pins.

Three properties, and the third is the one that keeps the fix honest: a probe we
STOPPED WAITING FOR must never read the same as a probe that completed and found
the service down.
"""
from __future__ import annotations

import importlib
import threading
import time

import pytest


@pytest.fixture
def reg(monkeypatch):
    """adapter_registry with a synthetic adapter map and no DB writes."""
    mod = importlib.import_module("tools.ops_hub.adapter_registry")
    monkeypatch.setattr(mod, "_persist_health", lambda *a, **kw: None)
    mod.invalidate_probe_cache()
    return mod


class _Health:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _FakeAdapter:
    """Adapter whose health_check blocks for *delay* seconds."""

    ADAPTER_TYPE = "oss"
    DOMAIN = "aiops"

    def __init__(self, name: str, delay: float = 0.0, available: bool = True,
                 gate: threading.Event | None = None) -> None:
        self.name = name
        self.delay = delay
        self.available = available
        self.gate = gate

    def health_check(self) -> _Health:
        if self.gate is not None:
            # Blocks until the test releases it, standing in for an adapter
            # whose network probe never returns.
            self.gate.wait(timeout=self.delay)
        elif self.delay:
            time.sleep(self.delay)
        return _Health({
            "available": self.available,
            "adapter_name": self.name,
            "adapter_type": self.ADAPTER_TYPE,
            "domain": self.DOMAIN,
            "version": "",
            "latency_ms": 0,
            "error": "" if self.available else "service reported down",
            "details": {},
        })


def _install(monkeypatch, reg, adapters: dict) -> None:
    monkeypatch.setattr(reg, "_ADAPTER_MAP", {n: f"fake.{n}" for n in adapters})
    monkeypatch.setattr(reg, "get_adapter", lambda name: adapters.get(name))


# ── 1. concurrency ───────────────────────────────────────────────────────────

def test_probe_all_runs_probes_concurrently(monkeypatch, reg):
    """Six 0.4s probes must cost about one of them, not the sum of six."""
    delay = 0.4
    count = 6
    adapters = {f"a{i}": _FakeAdapter(f"a{i}", delay=delay) for i in range(count)}
    _install(monkeypatch, reg, adapters)

    started = time.perf_counter()
    results = reg.probe_all(persist=False)
    elapsed = time.perf_counter() - started

    assert len(results) == count
    serial = delay * count
    assert elapsed < serial / 2, (
        f"probe_all took {elapsed:.2f}s for {count} x {delay}s probes; serial "
        f"would be {serial:.2f}s -- the probes are not running concurrently"
    )


def test_probe_all_preserves_adapter_order(monkeypatch, reg):
    """Concurrency must not reorder the grid: results follow _ADAPTER_MAP."""
    names = ["z_slow", "m_mid", "a_fast"]
    adapters = {
        "z_slow": _FakeAdapter("z_slow", delay=0.30),
        "m_mid": _FakeAdapter("m_mid", delay=0.15),
        "a_fast": _FakeAdapter("a_fast", delay=0.0),
    }
    _install(monkeypatch, reg, adapters)

    results = reg.probe_all(persist=False)
    assert [r["adapter_name"] for r in results] == names


# ── 2. the deadline ──────────────────────────────────────────────────────────

def test_probe_all_bounds_a_hung_adapter(monkeypatch, reg):
    """One adapter that never answers must not hold the whole page."""
    gate = threading.Event()
    adapters = {
        "fast": _FakeAdapter("fast", delay=0.0),
        "hung": _FakeAdapter("hung", delay=30.0, gate=gate),
    }
    _install(monkeypatch, reg, adapters)

    try:
        started = time.perf_counter()
        results = reg.probe_all(persist=False, timeout=0.5)
        elapsed = time.perf_counter() - started
    finally:
        gate.set()  # release the blocked worker thread

    assert elapsed < 5.0, (
        f"probe_all took {elapsed:.2f}s with a hung adapter and a 0.5s deadline "
        f"-- the deadline is not bounding the probe"
    )
    assert len(results) == 2
    by_name = {r["adapter_name"]: r for r in results}
    assert by_name["fast"]["available"] is True
    assert by_name["hung"]["available"] is False


# ── 3. a timeout is not a verdict about the service ──────────────────────────

def test_timed_out_probe_is_distinguishable_from_a_down_adapter(monkeypatch, reg):
    """`we stopped waiting` must never read as `we asked and it is down`."""
    gate = threading.Event()
    adapters = {
        "down": _FakeAdapter("down", delay=0.0, available=False),
        "hung": _FakeAdapter("hung", delay=30.0, gate=gate),
    }
    _install(monkeypatch, reg, adapters)

    try:
        results = reg.probe_all(persist=False, timeout=0.5)
    finally:
        gate.set()

    by_name = {r["adapter_name"]: r for r in results}
    # Both are unavailable -- that much they share.
    assert by_name["down"]["available"] is False
    assert by_name["hung"]["available"] is False
    # ...and nothing else. The states must differ.
    assert by_name["down"]["probe_state"] == "unavailable"
    assert by_name["hung"]["probe_state"] == "timeout"
    assert "timed out" in by_name["hung"]["error"].lower()


def test_a_raising_probe_is_not_a_service_verdict_either(monkeypatch, reg):
    """An adapter whose client blows up is `error`, not `unavailable`."""

    class _Boom:
        ADAPTER_TYPE = "csp"
        DOMAIN = "mlops"

        def health_check(self):
            raise RuntimeError("credential chain exploded")

    monkeypatch.setattr(reg, "_ADAPTER_MAP", {"boom": "fake.boom"})
    monkeypatch.setattr(reg, "get_adapter", lambda name: _Boom())

    results = reg.probe_all(persist=False)
    assert results[0]["available"] is False
    assert results[0]["probe_state"] == "error"
    assert "credential chain exploded" in results[0]["error"]


def test_a_completed_probe_reports_probe_state_ok(monkeypatch, reg):
    adapters = {"up": _FakeAdapter("up", delay=0.0, available=True)}
    _install(monkeypatch, reg, adapters)

    results = reg.probe_all(persist=False)
    assert results[0]["probe_state"] == "ok"
    assert results[0]["available"] is True


def test_unloadable_adapter_is_its_own_state(monkeypatch, reg):
    """An adapter class that would not import is neither down nor timed out."""
    monkeypatch.setattr(reg, "_ADAPTER_MAP", {"broken": "fake.broken"})
    monkeypatch.setattr(reg, "get_adapter", lambda name: None)

    results = reg.probe_all(persist=False)
    assert results[0]["available"] is False
    assert results[0]["probe_state"] == "unloadable"


# ── 4. persistence stays serial ──────────────────────────────────────────────

def test_persist_is_called_once_per_adapter_from_one_thread(monkeypatch, reg):
    """Probes fan out; DB writes do not -- one connection, one writer."""
    seen: list[tuple[str, int]] = []
    lock = threading.Lock()

    def record(name, health, now):
        with lock:
            seen.append((name, threading.get_ident()))

    monkeypatch.setattr(reg, "_persist_health", record)
    adapters = {f"a{i}": _FakeAdapter(f"a{i}", delay=0.05) for i in range(4)}
    _install(monkeypatch, reg, adapters)

    reg.probe_all(persist=True)

    assert sorted(n for n, _ in seen) == ["a0", "a1", "a2", "a3"]
    writer_threads = {tid for _, tid in seen}
    assert writer_threads == {threading.get_ident()}, (
        "persistence ran off the calling thread -- concurrent writes to one "
        "connection is a hazard the probe fan-out must not introduce"
    )
