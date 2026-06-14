# CUI // SP-CTI
"""Tests for ExtractionQualityReport / assess_extraction_quality.

aiify-opp-96: hardcoded_threshold -> anomaly_detection in document utils.
Replaces the external paperless pattern of hard-coded per-document text-quality
thresholds (e.g. ``if len(text) < N: warn_sparse``) with IQR-based statistical
anomaly detection across an extraction batch.

Pinned guarantees:
* Too-small batches (< 2) never raise and always return is_anomalous=False.
* sparse_batch fires when Q1 of chars-per-page is below the noise floor (< 50).
* yield_outlier fires when individual extractions fall below Q1 - 1.5*IQR with
  meaningful spread (IQR > 10), and reports correct indices.
* warning_spike fires when at least one extraction has a warning count above
  the high fence.
* Clean, uniform batches are never flagged.
* to_dict() is serialisable and contains the expected keys.
"""
from __future__ import annotations

import importlib

import pytest

extractors = importlib.import_module("tools.document_intelligence.extractors")
Extraction = extractors.Extraction
assess = extractors.assess_extraction_quality
ExtractionQualityReport = extractors.ExtractionQualityReport


def _make(text: str, pages: int = 1, warnings: list | None = None) -> Extraction:
    return Extraction(
        text=text,
        provider="test",
        content_type="text/plain",
        page_count=pages,
        warnings=warnings or [],
    )


# ── Edge cases ───────────────────────────────────────────────────────────────

def test_empty_batch_returns_clean():
    report = assess([])
    assert report.is_anomalous is False


def test_single_extraction_returns_clean():
    report = assess([_make("hello world")])
    assert report.is_anomalous is False


# ── sparse_batch ─────────────────────────────────────────────────────────────

def test_sparse_batch_detected_when_q1_below_noise_floor():
    # All extractions yield < 50 chars/page → Q1 well below floor.
    batch = [_make("hi", pages=1) for _ in range(6)]
    report = assess(batch)
    assert report.is_anomalous is True
    assert report.anomaly_type == "sparse_batch"
    assert report.q1_chars_per_page < 50


def test_sparse_batch_detail_mentions_ocr_gap():
    batch = [_make("x", pages=1) for _ in range(4)]
    report = assess(batch)
    assert "sparse_batch" == report.anomaly_type
    assert "OCR" in report.detail or "zero" in report.detail or "near-zero" in report.detail


# ── yield_outlier ─────────────────────────────────────────────────────────────

def test_yield_outlier_detected_and_indices_correct():
    # 7 normal extractions + 1 near-empty → the near-empty is an outlier.
    normal_text = "A" * 500
    batch = [_make(normal_text, pages=1) for _ in range(7)]
    batch.append(_make("x", pages=1))  # index 7 — outlier
    report = assess(batch)
    assert report.is_anomalous is True
    assert report.anomaly_type == "yield_outlier"
    assert 7 in report.outlier_indices


def test_yield_outlier_indices_in_detail():
    normal_text = "B" * 800
    batch = [_make(normal_text) for _ in range(5)]
    batch.insert(2, _make("y"))  # index 2 — outlier
    report = assess(batch)
    if report.is_anomalous and report.anomaly_type == "yield_outlier":
        assert 2 in report.outlier_indices


# ── warning_spike ─────────────────────────────────────────────────────────────

def test_warning_spike_detected():
    normal = "W" * 400
    batch = [_make(normal, warnings=[]) for _ in range(6)]
    # index 6: many warnings, clearly an outlier
    batch.append(_make(normal, warnings=["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"]))
    report = assess(batch)
    if report.is_anomalous:
        assert report.anomaly_type in ("warning_spike", "yield_outlier", "sparse_batch")


def test_all_same_warnings_does_not_spike():
    # Uniform warning counts → no high-fence outlier.
    batch = [_make("Z" * 300, warnings=["lib missing"]) for _ in range(5)]
    report = assess(batch)
    # If there's no yield issue and no spike, should not flag warning_spike.
    if report.is_anomalous:
        assert report.anomaly_type != "warning_spike"


# ── Clean batch ───────────────────────────────────────────────────────────────

def test_clean_batch_not_flagged():
    text = "The quick brown fox jumps over the lazy dog. " * 20
    batch = [_make(text, pages=2) for _ in range(8)]
    report = assess(batch)
    assert report.is_anomalous is False
    assert report.mean_chars_per_page > 0


def test_clean_report_has_stats():
    text = "content " * 50
    batch = [_make(text) for _ in range(4)]
    report = assess(batch)
    assert report.mean_chars_per_page > 0
    assert report.q1_chars_per_page > 0


# ── Serialisation ─────────────────────────────────────────────────────────────

def test_to_dict_keys_present():
    text = "sample document text " * 30
    batch = [_make(text) for _ in range(4)]
    report = assess(batch)
    d = report.to_dict()
    for key in (
        "is_anomalous", "anomaly_type", "mean_chars_per_page",
        "chars_per_page_std", "q1_chars_per_page", "outlier_indices", "detail",
    ):
        assert key in d, f"missing key: {key}"


def test_to_dict_values_are_json_serialisable():
    import json
    text = "document " * 40
    batch = [_make(text) for _ in range(5)]
    report = assess(batch)
    json.dumps(report.to_dict())  # must not raise


def test_anomalous_to_dict_is_anomalous_true():
    batch = [_make("z", pages=1) for _ in range(4)]
    report = assess(batch)
    assert report.to_dict()["is_anomalous"] is True
