# CUI // SP-CTI
"""Unit tests for the performance baseline harness (tools/testing/perf_benchmark.py).

These tests are **offline-safe** — they exercise the percentile math and the
regression-compare logic on synthetic timing data (no live server, no locust),
and assert that the harness self-skips cleanly when a live dashboard / locust is
absent. This keeps the harness from ever breaking the required Lint / Test /
Security / Helm CI checks (the CI Test job boots no dashboard).
"""
from __future__ import annotations

import importlib

import pytest

# Import via the shim-safe canonical path.
pb = importlib.import_module("tools.testing.perf_benchmark")


# ── Percentile math ─────────────────────────────────────────────────────────

def test_percentile_empty_is_zero():
    assert pb.percentile([], 95) == 0.0


def test_percentile_single_value():
    assert pb.percentile([42.0], 50) == 42.0
    assert pb.percentile([42.0], 99) == 42.0


def test_percentile_nearest_rank_known_values():
    # 1..100 → nearest-rank p50 = rank ceil(0.5*100)=50 → value 50
    data = [float(i) for i in range(1, 101)]
    assert pb.percentile(data, 50) == 50.0
    assert pb.percentile(data, 95) == 95.0
    assert pb.percentile(data, 99) == 99.0
    assert pb.percentile(data, 100) == 100.0


def test_percentile_is_monotonic():
    data = [5.0, 1.0, 3.0, 9.0, 7.0]
    p50 = pb.percentile(data, 50)
    p95 = pb.percentile(data, 95)
    p99 = pb.percentile(data, 99)
    assert p50 <= p95 <= p99


def test_summarize_shape_and_values():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    s = pb.summarize(data)
    assert s["count"] == 5
    assert s["min_ms"] == 10.0
    assert s["max_ms"] == 50.0
    assert s["mean_ms"] == 30.0
    assert s["p50_ms"] <= s["p95_ms"] <= s["p99_ms"]


def test_summarize_empty():
    s = pb.summarize([])
    assert s["count"] == 0
    assert s["p95_ms"] == 0.0


# ── Regression compare logic ────────────────────────────────────────────────

def _doc(backend, endpoints):
    return {"backend": backend, "endpoints": endpoints}


def test_compare_flags_regression_beyond_tolerance():
    baseline = _doc("sqlite", [{"endpoint": "/", "p95_ms": 100.0}])
    current = _doc("sqlite", [{"endpoint": "/", "p95_ms": 200.0}])  # +100%
    out = pb.compare_to_baseline(current, baseline, tolerance=0.5)
    assert out["comparable"] is True
    assert len(out["regressions"]) == 1
    assert out["regressions"][0]["endpoint"] == "/"
    assert out["regressions"][0]["ratio"] == 2.0


def test_compare_within_tolerance_is_not_regression():
    baseline = _doc("sqlite", [{"endpoint": "/", "p95_ms": 100.0}])
    current = _doc("sqlite", [{"endpoint": "/", "p95_ms": 140.0}])  # +40% < 50%
    out = pb.compare_to_baseline(current, baseline, tolerance=0.5)
    assert out["regressions"] == []


def test_compare_improvement_recorded():
    baseline = _doc("sqlite", [{"endpoint": "/", "p95_ms": 100.0}])
    current = _doc("sqlite", [{"endpoint": "/", "p95_ms": 60.0}])
    out = pb.compare_to_baseline(current, baseline, tolerance=0.5)
    assert out["regressions"] == []
    assert len(out["improvements"]) == 1


def test_placeholder_zeroed_baseline_never_regresses():
    # The committed placeholder baseline is all-zeros → everything unbaselined.
    baseline = _doc("sqlite", [{"endpoint": "/", "p95_ms": 0.0}])
    current = _doc("sqlite", [{"endpoint": "/", "p95_ms": 9999.0}])
    out = pb.compare_to_baseline(current, baseline, tolerance=0.5)
    assert out["regressions"] == []
    assert len(out["unbaselined"]) == 1


def test_backend_mismatch_refuses_comparison():
    baseline = _doc("postgresql", [{"endpoint": "/", "p95_ms": 100.0}])
    current = _doc("sqlite", [{"endpoint": "/", "p95_ms": 500.0}])
    out = pb.compare_to_baseline(current, baseline, tolerance=0.5)
    assert out["backend_match"] is False
    assert out["comparable"] is False
    assert out["regressions"] == []  # not comparable → no false alarms
    assert "note" in out


def test_backend_mismatch_can_be_forced():
    baseline = _doc("postgresql", [{"endpoint": "/", "p95_ms": 100.0}])
    current = _doc("sqlite", [{"endpoint": "/", "p95_ms": 500.0}])
    out = pb.compare_to_baseline(
        current, baseline, tolerance=0.5, allow_backend_mismatch=True
    )
    assert out["comparable"] is True
    assert len(out["regressions"]) == 1


# ── Committed baseline sanity ────────────────────────────────────────────────

def test_committed_baseline_loads_and_records_backend():
    baseline = pb.load_baseline()
    assert baseline is not None, "committed placeholder baseline must exist"
    assert baseline.get("backend") in ("sqlite", "postgresql")
    assert isinstance(baseline.get("endpoints"), list)
    assert len(baseline["endpoints"]) >= 5


# ── Self-skip behaviour (no live server / no locust) ─────────────────────────

def test_benchmark_skips_when_no_server(monkeypatch):
    monkeypatch.setattr(pb, "is_server_reachable", lambda *a, **k: False)
    with pytest.raises(RuntimeError):
        pb.run_benchmark(base="http://127.0.0.1:59999")


def test_load_skips_gracefully_when_locust_absent(monkeypatch):
    monkeypatch.setattr(pb, "locust_available", lambda: False)
    out = pb.run_load(base="http://127.0.0.1:59999")
    assert out["skipped"] is True
    assert "locust" in out["reason"].lower()


def test_load_skips_gracefully_when_no_server(monkeypatch):
    # locust "present" but no server → still skipped, never raises.
    monkeypatch.setattr(pb, "locust_available", lambda: True)
    monkeypatch.setattr(pb, "is_server_reachable", lambda *a, **k: False)
    out = pb.run_load(base="http://127.0.0.1:59999")
    assert out["skipped"] is True


def test_locustfile_imports_without_locust():
    # The locustfile must import even when locust is not installed.
    mod = importlib.import_module("tools.testing.perf.locustfile")
    assert hasattr(mod, "HotEndpointsUser")
    assert isinstance(mod.HOT_GET_ENDPOINTS, list)
