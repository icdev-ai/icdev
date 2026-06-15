# CUI // SP-CTI
"""Tests for DIC adaptive document filters (anomaly detection)."""
from __future__ import annotations

from tools.document_intelligence.filters import (
    _adaptive_bounds,
    _iqr_bounds,
    _zscore_bounds,
    anomaly_report,
    filter_by_age,
    filter_by_relevance,
    filter_by_size,
)


# ── Unit: bound algorithms ────────────────────────────────────────────────────

def test_iqr_bounds_rejects_too_few_values():
    lo, hi = _iqr_bounds([0.5, 0.6, 0.7])
    assert lo == 0.0
    assert hi == float("inf")


def test_iqr_bounds_with_outlier():
    values = [0.5] * 8 + [100.0]  # 100.0 is a clear outlier
    _, hi = _iqr_bounds(values)
    assert hi < 100.0


def test_zscore_bounds_symmetrical():
    values = [float(i) for i in range(100)]
    lo, hi = _zscore_bounds(values, z=2.5)
    mid = (lo + hi) / 2
    assert abs(mid - 49.5) < 1.0  # centre ≈ mean


def test_adaptive_bounds_switches_algorithm():
    small = [float(i) for i in range(10)]
    large = [float(i) for i in range(50)]
    lo_s, hi_s = _iqr_bounds(small)
    lo_l, hi_l = _zscore_bounds(large)
    # Adaptive should match the expected algorithm for each size
    assert _adaptive_bounds(small) == _iqr_bounds(small)
    assert _adaptive_bounds(large) == _zscore_bounds(large)


# ── filter_by_relevance ───────────────────────────────────────────────────────

def _make_docs(n: int) -> list[dict]:
    return [{"doc_id": f"doc-{i}"} for i in range(n)]


def test_filter_by_relevance_all_pass():
    docs = _make_docs(10)
    scores = [0.9] * 10
    kept, anomalies = filter_by_relevance(docs, scores)
    assert len(kept) == 10
    assert len(anomalies) == 0


def test_filter_by_relevance_outlier_flagged():
    docs = _make_docs(20)
    scores = [0.8] * 19 + [0.001]  # last doc is a clear outlier
    kept, anomalies = filter_by_relevance(docs, scores)
    assert len(anomalies) >= 1
    assert anomalies[-1]["doc_id"] == "doc-19"


def test_filter_by_relevance_floor_enforced():
    docs = _make_docs(5)
    scores = [0.0, 0.01, 0.02, 0.03, 0.04]  # all very low
    kept, anomalies = filter_by_relevance(docs, scores, floor=0.05)
    # All should be filtered as anomalies (below floor)
    assert len(anomalies) == 5


def test_filter_by_relevance_length_mismatch_returns_all():
    docs = _make_docs(3)
    kept, anomalies = filter_by_relevance(docs, [0.5, 0.6])  # length mismatch
    assert len(kept) == 3
    assert len(anomalies) == 0


# ── filter_by_size ────────────────────────────────────────────────────────────

def test_filter_by_size_normal_pass():
    docs = [{"doc_id": f"d{i}", "file_size_bytes": 1_000_000} for i in range(10)]
    kept, anomalies = filter_by_size(docs)
    assert len(kept) == 10


def test_filter_by_size_giant_file_flagged():
    docs = [{"doc_id": f"d{i}", "file_size_bytes": 1_000_000} for i in range(19)]
    docs.append({"doc_id": "giant", "file_size_bytes": 2_000_000_000})  # 2 GB
    kept, anomalies = filter_by_size(docs)
    flagged_ids = [a["doc_id"] for a in anomalies]
    assert "giant" in flagged_ids


def test_filter_by_size_absolute_ceiling():
    # 600 MB > _MAX_DOC_SIZE_MB (500) — must always be flagged
    docs = [{"doc_id": "big", "file_size_bytes": 600 * 1_048_576}]
    kept, anomalies = filter_by_size(docs, ceiling_mb=500.0)
    assert any(a["doc_id"] == "big" for a in anomalies)


def test_filter_by_size_missing_key_treated_as_zero():
    docs = [{"doc_id": "nodim"}]
    kept, _ = filter_by_size(docs)
    assert len(kept) == 1


# ── filter_by_age ─────────────────────────────────────────────────────────────

def test_filter_by_age_recent_docs_pass():
    docs = [{"doc_id": f"d{i}", "created_at": "2026-06-01T00:00:00Z"} for i in range(10)]
    kept, anomalies = filter_by_age(docs)
    assert len(kept) == 10


def test_filter_by_age_very_old_flagged():
    recent = [{"doc_id": f"d{i}", "created_at": "2025-01-01T00:00:00Z"} for i in range(19)]
    ancient = {"doc_id": "ancient", "created_at": "1990-01-01T00:00:00Z"}
    kept, anomalies = filter_by_age(recent + [ancient])
    assert any(a["doc_id"] == "ancient" for a in anomalies)


def test_filter_by_age_bad_date_treated_as_zero_age():
    docs = [{"doc_id": "bad", "created_at": "not-a-date"}]
    kept, _ = filter_by_age(docs)
    assert len(kept) == 1


# ── anomaly_report ────────────────────────────────────────────────────────────

def test_anomaly_report_structure():
    docs = _make_docs(5)
    scores = [0.7] * 5
    report = anomaly_report(docs, scores)
    assert "total" in report
    assert "dimensions" in report
    assert "total_anomalies" in report
    assert report["total"] == 5


def test_anomaly_report_no_scores():
    docs = _make_docs(3)
    report = anomaly_report(docs, scores=None)
    assert "relevance" not in report["dimensions"]
    assert "size" in report["dimensions"]
    assert "age" in report["dimensions"]


def test_anomaly_report_deduplicates_anomaly_ids():
    # Same doc could be anomalous in multiple dimensions
    docs = [
        {"doc_id": "outlier", "file_size_bytes": 2_000_000_000, "created_at": "1990-01-01T00:00:00Z"},
        *[{"doc_id": f"d{i}", "file_size_bytes": 1_000_000, "created_at": "2026-01-01T00:00:00Z"} for i in range(19)],
    ]
    report = anomaly_report(docs)
    ids = report["anomaly_doc_ids"]
    assert ids.count("outlier") <= 1  # deduped
