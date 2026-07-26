# CUI // SP-CTI
"""Latency capture in the retrieval benchmark (oss-meas-01).

The task asked for "latency delta" per toggle and the first cut of the sweep did
not collect it. That was not cosmetic: `binary_prefilter` is a *speed*
optimisation whose correct outcome is a ZERO quality delta — it shrinks the
candidate set without changing what comes back — so a quality-only sweep cannot
decide it at all, and reported `0.0000` across every metric with no way to tell
"working as designed" from "doing nothing".
"""
from __future__ import annotations

import pytest

from tools.rag import rag_benchmark as rb


# ── Percentiles ───────────────────────────────────────────────────────────────


def test_empty_samples_report_none_not_zero():
    """Zero would read as 'instant'; None reads as 'not measured'."""
    stats = rb.latency_stats([])
    assert stats["n"] == 0
    for key in ("p50_ms", "p95_ms", "mean_ms", "total_ms"):
        assert stats[key] is None


def test_percentiles_are_observed_values_not_interpolated():
    samples = [float(x) for x in range(1, 101)]     # 1..100 ms
    stats = rb.latency_stats(samples)
    assert stats["p50_ms"] == 50.0
    assert stats["p95_ms"] == 95.0
    assert stats["p50_ms"] in samples and stats["p95_ms"] in samples


def test_single_sample_is_its_own_percentiles():
    stats = rb.latency_stats([42.0])
    assert stats["p50_ms"] == stats["p95_ms"] == stats["mean_ms"] == 42.0
    assert stats["n"] == 1


def test_unsorted_input_is_ordered_before_ranking():
    stats = rb.latency_stats([100.0, 1.0, 50.0])
    assert stats["p50_ms"] == 50.0
    assert stats["p95_ms"] == 100.0


def test_p95_catches_a_tail_the_mean_hides():
    """A toggle that helps the median and wrecks the tail is a regression.

    The tail is 10% here, not 5%: with exactly 5% slow samples the nearest-rank
    p95 lands on the last *fast* value, which is arithmetically correct and would
    make this test assert the opposite of what it means to.
    """
    fast_median_bad_tail = [10.0] * 90 + [900.0] * 10
    stats = rb.latency_stats(fast_median_bad_tail)
    assert stats["p50_ms"] == 10.0
    assert stats["p95_ms"] == 900.0, "tail must be visible"
    assert stats["mean_ms"] < 100.0, "the mean alone would look fine"


# ── Wiring into the run ───────────────────────────────────────────────────────


def _golden(n: int = 3) -> dict:
    return {
        "version": "test",
        "top_k": 5,
        "queries": [
            {"id": f"q{i}", "query": f"question {i}", "expect": {"substrings": ["alpha"]}}
            for i in range(n)
        ],
    }


class _Hit:
    def __init__(self, content="alpha beta", chunk_id="c1"):
        self.content, self.chunk_id, self.source_id = content, chunk_id, "s1"


def test_run_reports_latency_for_every_scored_query():
    bench = rb.RAGBenchmark(golden_set=_golden(3))
    result = bench.run(search_fn=lambda q, k: [_Hit()])

    assert result["latency"]["n"] == 3
    assert all("latency_ms" in r for r in result["results"])
    assert all(r["latency_ms"] >= 0 for r in result["results"])


def test_latency_is_not_inside_aggregate():
    """`aggregate` holds quality scores in [0,1] that a comparison subtracts blindly.

    Latency is milliseconds and lower-is-better; mixing it in would make
    compare_to_baseline report a slowdown as a positive delta next to real gains.
    """
    result = rb.RAGBenchmark(golden_set=_golden()).run(search_fn=lambda q, k: [_Hit()])
    assert "latency" not in result["aggregate"]
    for key, value in result["aggregate"].items():
        assert value is None or 0.0 <= value <= 1.0, f"{key} is not a [0,1] score"


def test_a_failing_query_contributes_no_latency_sample():
    """A query that raised was never timed; it must not enter the distribution."""
    calls = {"n": 0}

    def flaky(query, k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("retriever down")
        return [_Hit()]

    result = rb.RAGBenchmark(golden_set=_golden(3)).run(search_fn=flaky)
    assert result["latency"]["n"] == 2, "the failed query must not be counted"
    errored = [r for r in result["results"] if "error" in r]
    assert len(errored) == 1
    assert "latency_ms" not in errored[0]


def test_sweep_reports_latency_delta_separately_from_quality(monkeypatch):
    """A toggle can be a KEEP on quality and a DROP on cost; keep them apart."""
    class _Stub:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return {
                "aggregate": {"recall_at_5": 0.5, "mrr": 0.4},
                "latency": {"n": 3, "p50_ms": 10.0, "p95_ms": 20.0, "mean_ms": 12.0},
                "queries_scored": 3,
            }

    monkeypatch.setattr(rb, "RAGBenchmark", _Stub)
    sweep = rb.run_toggle_sweep(only=["rerank"])

    arm = next(a for a in sweep["arms"] if a["toggle"] == "rerank")
    assert "latency_delta" in arm
    assert set(arm["latency_delta"]) == {"p50_ms", "p95_ms", "mean_ms"}
    assert "latency" in sweep["control"]
    # identical stub both sides -> zero cost delta, and quality deltas untouched
    assert arm["latency_delta"]["p50_ms"]["delta"] == 0.0
    assert "p50_ms" not in arm["deltas"], "latency must not leak into quality deltas"


@pytest.mark.parametrize("stat", ["p50_ms", "p95_ms", "mean_ms"])
def test_latency_delta_survives_a_missing_stat(stat):
    """A zeroed or partial arm must not crash the sweep."""
    d = rb._delta(None, 12.0)
    assert d["delta"] is None and d["current"] == 12.0
