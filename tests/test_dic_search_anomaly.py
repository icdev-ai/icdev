"""Tests for search-result anomaly detection in the DIC search engine.

aiify-opp-68 (hardcoded_threshold -> anomaly_detection): the external
_backend.py hard-codes a single relevance cutoff to decide whether to keep a
result.  The DIC equivalent replaces that pattern with IQR-based distribution
analysis via ``detect_search_anomalies`` so no hand-tuned constant is needed.

Guarantees pinned here:

* an empty result set returns a non-anomalous report with mean=0.0;
* a single result is non-anomalous regardless of its score;
* ``low_coverage``: when Q3 of attribution scores is below 0.05;
* ``score_outlier``: when the top score exceeds mean + 3σ with meaningful spread;
* ``high_variance``: when std > 0.3 and IQR > 0.25;
* a healthy spread of moderate scores produces no anomaly;
* anomaly reports serialize correctly via ``to_dict()``;
* ``DICGroundedSearch.to_dict()`` includes ``anomaly_report`` when present.
"""
from __future__ import annotations

import importlib

se = importlib.import_module("tools.document_intelligence.search_engine")

detect = se.detect_search_anomalies
DICSearchResult = se.DICSearchResult
DICGroundedSearch = se.DICGroundedSearch
SearchAnomalyReport = se.SearchAnomalyReport


def _result(attribution: float) -> DICSearchResult:
    r = DICSearchResult(chunk_id="c", doc_id="d", content="x")
    r.attribution_score = attribution
    return r


# ── Empty / single-result edge cases ──────────────────────────────────────────

def test_empty_results_not_anomalous():
    report = detect([])
    assert not report.is_anomalous
    assert report.mean_attribution == 0.0
    assert report.anomaly_type == ""


def test_single_result_not_anomalous():
    report = detect([_result(0.0)])
    assert not report.is_anomalous
    assert report.mean_attribution == 0.0


def test_single_high_result_not_anomalous():
    report = detect([_result(0.9)])
    assert not report.is_anomalous


# ── low_coverage ──────────────────────────────────────────────────────────────

def test_low_coverage_all_near_zero():
    results = [_result(s) for s in [0.01, 0.02, 0.01, 0.03]]
    report = detect(results)
    assert report.is_anomalous
    assert report.anomaly_type == "low_coverage"


def test_low_coverage_q3_exactly_at_floor():
    # Q3 = sorted[3//4 * 4] = sorted[3] = 0.049 < 0.05
    results = [_result(s) for s in [0.01, 0.02, 0.03, 0.049]]
    report = detect(results)
    assert report.is_anomalous
    assert report.anomaly_type == "low_coverage"


def test_not_low_coverage_when_q3_above_floor():
    # Q3 should be 0.1 (above 0.05)
    results = [_result(s) for s in [0.01, 0.05, 0.08, 0.1]]
    report = detect(results)
    # might be another anomaly type, but NOT low_coverage
    assert report.anomaly_type != "low_coverage"


# ── score_outlier ─────────────────────────────────────────────────────────────

def test_score_outlier_single_dominant_result():
    # One result at 0.95, rest near 0.1 — top >> mean + 3σ
    results = [_result(s) for s in [0.1, 0.1, 0.1, 0.1, 0.95]]
    report = detect(results)
    assert report.is_anomalous
    assert report.anomaly_type == "score_outlier"
    assert report.top_score == 0.95


def test_score_outlier_requires_meaningful_spread():
    # All same value except one slightly higher — std is tiny, not an outlier
    results = [_result(s) for s in [0.5, 0.5, 0.5, 0.5, 0.51]]
    report = detect(results)
    assert not report.is_anomalous


# ── high_variance ─────────────────────────────────────────────────────────────

def test_high_variance_wide_spread():
    # Scores spanning 0 to 1 with high IQR
    results = [_result(s) for s in [0.0, 0.1, 0.5, 0.9, 1.0]]
    report = detect(results)
    assert report.is_anomalous
    assert report.anomaly_type == "high_variance"


def test_high_variance_requires_both_std_and_iqr():
    # Low IQR even if some variance — not high_variance
    results = [_result(s) for s in [0.4, 0.45, 0.5, 0.55, 0.6]]
    report = detect(results)
    assert not report.is_anomalous


# ── healthy results ───────────────────────────────────────────────────────────

def test_healthy_uniform_moderate_scores():
    results = [_result(s) for s in [0.3, 0.35, 0.4, 0.45, 0.5]]
    report = detect(results)
    assert not report.is_anomalous
    assert report.anomaly_type == ""


def test_healthy_tight_high_scores():
    results = [_result(s) for s in [0.7, 0.75, 0.8, 0.82, 0.85]]
    report = detect(results)
    assert not report.is_anomalous


# ── serialization ─────────────────────────────────────────────────────────────

def test_anomaly_report_to_dict():
    results = [_result(s) for s in [0.1, 0.1, 0.1, 0.1, 0.95]]
    report = detect(results)
    d = report.to_dict()
    assert isinstance(d["is_anomalous"], bool)
    assert isinstance(d["anomaly_type"], str)
    assert isinstance(d["mean_attribution"], float)
    assert isinstance(d["score_std"], float)
    assert isinstance(d["top_score"], float)
    assert isinstance(d["detail"], str)


def test_grounded_search_to_dict_includes_anomaly_report():
    results = [_result(s) for s in [0.01, 0.01, 0.01, 0.01]]
    report = detect(results)
    gs = DICGroundedSearch(
        query="test",
        results=results,
        result_count=len(results),
        citation_quality=0.01,
        anomaly_report=report,
    )
    d = gs.to_dict()
    assert "anomaly_report" in d
    assert d["anomaly_report"]["anomaly_type"] == "low_coverage"


def test_grounded_search_to_dict_no_anomaly_report_when_none():
    gs = DICGroundedSearch(
        query="test",
        results=[],
        result_count=0,
        citation_quality=0.0,
        anomaly_report=None,
    )
    d = gs.to_dict()
    assert "anomaly_report" not in d
