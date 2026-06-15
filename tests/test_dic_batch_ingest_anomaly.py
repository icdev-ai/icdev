"""Tests for batch ingest processing-time anomaly detection.

aiify-opp-35: hardcoded_threshold -> anomaly_detection in the document
management command base class (paperless base.py). The DIC analog is
ingest_batch() + _detect_processing_time_anomalies() — IQR-based outlier
detection on per-document elapsed times instead of a hardcoded PROGRESS_STEP.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
_detect = orch._detect_processing_time_anomalies
ingest_batch = orch.ingest_batch
BatchIngestResult = orch.BatchIngestResult


# ── IQR helper ───────────────────────────────────────────────────────────────

def test_detect_too_few_samples_returns_inf():
    lo, hi = _detect([1.0, 2.0, 3.0])  # < 4 values
    assert lo == float("-inf")
    assert hi == float("inf")


def test_detect_exactly_four_samples():
    lo, hi = _detect([1.0, 2.0, 3.0, 4.0])
    assert lo < 1.0
    assert hi > 4.0


def test_detect_tight_cluster_flags_outlier():
    base = [1.0, 1.1, 1.2, 1.0, 1.1, 1.2, 1.0, 1.1]
    lo, hi = _detect(base)
    assert hi < 5.0, "fence should be tight for a uniform cluster"
    # an outlier of 10.0 should exceed hi
    assert 10.0 > hi


def test_detect_uniform_distribution_no_false_positives():
    times = [float(i) for i in range(10)]  # 0..9, uniformly spread
    lo, hi = _detect(times)
    for t in times:
        assert lo <= t <= hi, f"{t} should not be flagged as outlier in uniform spread"


def test_detect_fence_parameter_honored():
    times = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 5.0]
    _, hi_tight = _detect(times, fence=0.5)
    _, hi_loose = _detect(times, fence=3.0)
    assert hi_tight <= hi_loose


# ── ingest_batch ─────────────────────────────────────────────────────────────

def _make_mock_outcome(doc_id: str = "doc-1"):
    outcome = MagicMock()
    outcome.doc_id = doc_id
    return outcome


def test_ingest_batch_empty_list():
    result = ingest_batch([], "col-1")
    assert isinstance(result, BatchIngestResult)
    assert result.total == 0
    assert result.succeeded == 0
    assert result.failed == 0
    assert result.per_file == []
    assert result.anomalous_paths == []


def test_ingest_batch_all_succeed(tmp_path):
    files = []
    for i in range(4):
        f = tmp_path / f"doc_{i}.txt"
        f.write_text(f"document {i}")
        files.append(f)

    outcomes = [_make_mock_outcome(f"doc-{i}") for i in range(4)]

    with patch.object(orch, "ingest_file", side_effect=outcomes):
        result = ingest_batch(files, "col-test")

    assert result.total == 4
    assert result.succeeded == 4
    assert result.failed == 0
    assert all(r["ok"] for r in result.per_file)
    assert all("elapsed_s" in r for r in result.per_file)


def test_ingest_batch_partial_failure(tmp_path):
    files = []
    for i in range(3):
        f = tmp_path / f"doc_{i}.txt"
        f.write_text(f"document {i}")
        files.append(f)

    def _side_effect(path, *args, **kwargs):
        if "doc_1" in str(path):
            raise RuntimeError("extraction failed")
        return _make_mock_outcome("ok-doc")

    with patch.object(orch, "ingest_file", side_effect=_side_effect):
        result = ingest_batch(files, "col-test")

    assert result.total == 3
    assert result.succeeded == 2
    assert result.failed == 1
    failed = [r for r in result.per_file if not r["ok"]]
    assert len(failed) == 1
    assert "extraction failed" in failed[0]["error"]


def test_ingest_batch_anomalous_detection(tmp_path, monkeypatch):
    """Simulate 6 fast docs + 1 very slow outlier; the slow one should be flagged.

    monotonic returns paired (start, end) values so elapsed = end - start.
    Docs 1-6 each take ~0.01s; doc 7 takes ~100s.
    With 7 samples [0.01]*6 + [100.0]: q1=q3=0.01, IQR=0, hi=0.01 — 100 > 0.01.
    """
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    files = [f] * 7

    # 14 monotonic calls (2 per doc): start + end pairs
    fake_times = [
        0.00, 0.01,   # doc 1: elapsed=0.01
        0.01, 0.02,   # doc 2
        0.02, 0.03,   # doc 3
        0.03, 0.04,   # doc 4
        0.04, 0.05,   # doc 5
        0.05, 0.06,   # doc 6
        0.06, 100.06, # doc 7: elapsed=100.0 — anomalous outlier
    ]
    fake_iter = iter(fake_times)

    import time as _time
    monkeypatch.setattr(_time, "monotonic", lambda: next(fake_iter))

    call_count = [0]

    def _side_effect(path, *args, **kwargs):
        call_count[0] += 1
        return _make_mock_outcome(f"doc-{call_count[0]}")

    with patch.object(orch, "ingest_file", side_effect=_side_effect):
        result = ingest_batch(files, "col-test")

    # The 7th document has elapsed ≈ 100s; IQR on [0.01]*6 + [100.0] flags it.
    assert result.total == 7
    assert result.succeeded == 7
    assert len(result.anomalous_paths) >= 1
    # Verify the anomalous doc is the last one (100s)
    assert any(r["anomalous"] for r in result.per_file), "at least one doc should be anomalous"


def test_ingest_batch_progress_cb_called(tmp_path):
    files = []
    for i in range(10):
        f = tmp_path / f"doc_{i}.txt"
        f.write_text(f"doc {i}")
        files.append(f)

    calls = []

    def _cb(done, total, anomalous):
        calls.append((done, total))

    with patch.object(orch, "ingest_file", return_value=_make_mock_outcome()):
        ingest_batch(files, "col-test", progress_cb=_cb)

    assert len(calls) >= 1
    for done, total in calls:
        assert total == 10
        assert 1 <= done <= 10


def test_batch_ingest_result_to_dict():
    r = BatchIngestResult(
        total=2,
        succeeded=2,
        failed=0,
        per_file=[{"path": "a.txt", "ok": True, "doc_id": "d1", "elapsed_s": 0.1,
                   "anomalous": False, "error": ""}],
        anomalous_paths=[],
    )
    d = r.to_dict()
    assert d["total"] == 2
    assert d["succeeded"] == 2
    assert d["anomalous_paths"] == []
