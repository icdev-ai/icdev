"""Tests for filter-predicate coverage anomaly detection in the DIC search engine.

aiify-opp-31 (hardcoded_threshold -> anomaly_detection): the external
src/documents/filters.py hard-codes filter thresholds (e.g. ``min_pages=5``,
``confidence >= 0.7``) that must be hand-tuned per deployment.  The DIC
equivalent replaces that pattern with IQR-based distribution analysis via
``detect_filter_coverage_anomaly`` so no hand-tuned constant is needed.

Guarantees pinned here:

* fewer than 4 candidate values returns a non-anomalous report;
* ``over_restrictive`` (min direction): threshold above upper IQR fence AND < 10% coverage;
* ``over_restrictive`` (max direction): threshold below lower IQR fence AND < 10% coverage;
* ``over_permissive`` (min direction): threshold below lower IQR fence AND > 90% coverage;
* ``over_permissive`` (max direction): threshold above upper IQR fence AND > 90% coverage;
* healthy thresholds with moderate coverage produce no anomaly;
* ``suggested_threshold`` is present in the dict only when an anomaly is flagged;
* anomaly reports serialize correctly via ``to_dict()``.
"""
from __future__ import annotations

import importlib

se = importlib.import_module("tools.document_intelligence.search_engine")

detect = se.detect_filter_coverage_anomaly
DICFilterCoverageAnomaly = se.DICFilterCoverageAnomaly


# ── Edge cases: too few values ────────────────────────────────────────────────

def test_empty_values_not_anomalous():
    report = detect([], threshold=0.5)
    assert not report.is_anomalous
    assert report.anomaly_type == ""


def test_three_values_not_anomalous():
    report = detect([0.1, 0.5, 0.9], threshold=0.3)
    assert not report.is_anomalous


# ── over_restrictive (min direction) ─────────────────────────────────────────

def test_over_restrictive_min_high_threshold():
    # Values cluster around 0.1-0.3; threshold of 0.99 is far above Q3+1.5*IQR
    values = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45]
    report = detect(values, threshold=0.99, direction="min")
    assert report.is_anomalous
    assert report.anomaly_type == "over_restrictive"
    assert report.coverage_pct < 0.10
    assert report.suggested_threshold is not None
    # Suggested should be near Q3
    assert report.suggested_threshold == report.q3


def test_over_restrictive_min_zero_coverage():
    # All values below threshold — none pass
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    report = detect(values, threshold=100.0, direction="min")
    assert report.is_anomalous
    assert report.anomaly_type == "over_restrictive"
    assert report.coverage_pct == 0.0


# ── over_permissive (min direction) ──────────────────────────────────────────

def test_over_permissive_min_low_threshold():
    # Values cluster around 0.6-0.9; threshold of 0.001 admits everything
    values = [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    report = detect(values, threshold=0.001, direction="min")
    assert report.is_anomalous
    assert report.anomaly_type == "over_permissive"
    assert report.coverage_pct > 0.90
    assert report.suggested_threshold is not None
    assert report.suggested_threshold == report.q1


def test_over_permissive_min_full_coverage():
    # Threshold of 0.0 means everything passes
    values = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]
    report = detect(values, threshold=0.0, direction="min")
    assert report.is_anomalous
    assert report.anomaly_type == "over_permissive"
    assert report.coverage_pct == 1.0


# ── over_restrictive (max direction) ─────────────────────────────────────────

def test_over_restrictive_max_low_threshold():
    # Values cluster around 50-200 pages; max threshold of 1 admits almost none
    values = [50, 75, 100, 120, 150, 175, 200, 225]
    report = detect(
        [float(v) for v in values], threshold=1.0, direction="max"
    )
    assert report.is_anomalous
    assert report.anomaly_type == "over_restrictive"
    assert report.coverage_pct < 0.10


# ── over_permissive (max direction) ──────────────────────────────────────────

def test_over_permissive_max_high_threshold():
    # Values cluster around 5-20 pages; max threshold of 10000 admits everything
    values = [5, 8, 10, 12, 15, 18, 20, 22]
    report = detect(
        [float(v) for v in values], threshold=10000.0, direction="max"
    )
    assert report.is_anomalous
    assert report.anomaly_type == "over_permissive"
    assert report.coverage_pct == 1.0


# ── healthy thresholds ────────────────────────────────────────────────────────

def test_healthy_min_moderate_coverage():
    # Values 0.0-1.0; threshold at 0.5 gives ~50% coverage — not anomalous
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    report = detect(values, threshold=0.5, direction="min")
    assert not report.is_anomalous
    assert report.anomaly_type == ""


def test_healthy_max_moderate_coverage():
    values = [10, 20, 30, 40, 50, 60, 70, 80]
    report = detect([float(v) for v in values], threshold=50.0, direction="max")
    assert not report.is_anomalous


def test_healthy_min_tight_high_range():
    # All values high; threshold just below min still gets good coverage
    values = [0.8, 0.82, 0.85, 0.87, 0.9, 0.92, 0.95, 0.98]
    report = detect(values, threshold=0.79, direction="min")
    assert not report.is_anomalous


# ── IQR computed correctly ────────────────────────────────────────────────────

def test_iqr_and_quartiles_populated():
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    report = detect(values, threshold=0.5, direction="min")
    assert report.q1 > 0.0
    assert report.q3 > report.q1
    # iqr is computed as q3 - q1 (floating point); verify it's consistent
    assert abs(report.iqr - (report.q3 - report.q1)) < 1e-9


# ── Serialization ─────────────────────────────────────────────────────────────

def test_to_dict_no_suggested_when_healthy():
    values = [0.1, 0.3, 0.5, 0.7, 0.9, 1.0, 0.6, 0.4]
    report = detect(values, threshold=0.5, direction="min")
    d = report.to_dict()
    assert isinstance(d["is_anomalous"], bool)
    assert "suggested_threshold" not in d  # healthy → no suggestion


def test_to_dict_has_suggested_when_anomalous():
    values = [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    report = detect(values, threshold=0.001, direction="min")
    assert report.is_anomalous
    d = report.to_dict()
    assert "suggested_threshold" in d
    assert isinstance(d["suggested_threshold"], float)


def test_to_dict_keys_present():
    values = [0.1, 0.2, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    report = detect(values, threshold=0.5, direction="min")
    d = report.to_dict()
    for key in ("is_anomalous", "anomaly_type", "coverage_pct", "q1", "q3", "iqr", "detail"):
        assert key in d, f"Missing key: {key}"


def test_coverage_pct_is_float_between_0_and_1():
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    report = detect(values, threshold=0.5, direction="min")
    assert 0.0 <= report.coverage_pct <= 1.0
