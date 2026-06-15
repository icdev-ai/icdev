"""Tests for validate_import_documents() — import-time size/content anomaly detection.

aiify-opp-47: hardcoded_threshold -> anomaly_detection in the document_importer
management command analog in DIC. Validates that:
  - absolute file-size cap (DIC_IMPORT_MAX_FILE_BYTES) is applied
  - IQR-based outlier detection flags batch size outliers
  - near-empty content detection fires correctly
  - _detect_file_size_anomalies returns (-inf, inf) for <4 samples
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
_detect = orch._detect_file_size_anomalies
validate = orch.validate_import_documents
ArchiveImportResult = orch.ArchiveImportResult
ImportValidationResult = orch.ImportValidationResult


# ── _detect_file_size_anomalies ───────────────────────────────────────────────

def test_size_iqr_too_few_samples():
    lo, hi = _detect([100, 200, 300])  # < 4 values
    assert lo == float("-inf")
    assert hi == float("inf")


def test_size_iqr_tight_cluster_finite_fence():
    sizes = [1000, 1100, 1050, 1080, 990, 1020, 1060, 1010]
    lo, hi = _detect(sizes)
    assert hi < 10_000, "fence should be finite for a tight cluster"


def test_size_iqr_upper_outlier_detected():
    sizes = [1000, 1100, 1050, 1080, 990, 1020, 1060, 1010]
    _, hi = _detect(sizes)
    assert 5_000_000 > hi, "extreme outlier should exceed the fence"


def test_size_iqr_four_identical():
    lo, hi = _detect([500, 500, 500, 500])
    assert lo == 500.0
    assert hi == 500.0


# ── validate_import_documents — absolute size gate ────────────────────────────

def test_absolute_size_gate_rejects_oversized(tmp_path, monkeypatch):
    monkeypatch.setenv("DIC_IMPORT_MAX_FILE_BYTES", "100")
    # Reload constants after env change
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * 200)

    # Override the module constant directly (env var applied at import time).
    orig = orch._IMPORT_MAX_FILE_BYTES
    orch._IMPORT_MAX_FILE_BYTES = 100
    try:
        result = validate([big])
    finally:
        orch._IMPORT_MAX_FILE_BYTES = orig

    assert result.total == 1
    assert result.rejected == 1
    assert result.accepted == 0
    rec = result.per_file[0]
    assert not rec.accepted
    assert "exceeds_max_size" in rec.rejection_reason


def test_small_file_accepted(tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("Hello world document content.", encoding="utf-8")

    orig = orch._IMPORT_MAX_FILE_BYTES
    orch._IMPORT_MAX_FILE_BYTES = 100 * 1024 * 1024
    try:
        result = validate([small])
    finally:
        orch._IMPORT_MAX_FILE_BYTES = orig

    assert result.accepted == 1
    assert result.rejected == 0


# ── validate_import_documents — IQR outlier detection ────────────────────────

def test_iqr_outlier_flagged_as_anomalous(tmp_path, monkeypatch):
    """A file 10× larger than its siblings should be flagged anomalous."""
    monkeypatch.setattr(orch, "_IMPORT_MAX_FILE_BYTES", 200 * 1024 * 1024)
    monkeypatch.setattr(orch, "_IMPORT_SIZE_IQR_FENCE", 1.5)

    files = []
    # 8 small ~1 KB sibling files
    for i in range(8):
        f = tmp_path / f"doc_{i}.txt"
        f.write_bytes(b"A" * 1024)
        files.append(f)
    # 1 large outlier ~1 MB
    big = tmp_path / "outlier.txt"
    big.write_bytes(b"B" * (1024 * 1024))
    files.append(big)

    # Prevent LLM call by patching the quality checker
    def _mock_quality(*args, **kwargs):
        return "suspicious", "mocked"
    monkeypatch.setattr(orch, "_llm_import_quality_check", _mock_quality)

    result = validate(files)

    anomalous_paths = [r.path for r in result.per_file if r.anomalous]
    assert str(big) in anomalous_paths, (
        f"outlier not flagged; anomalous={anomalous_paths}"
    )


# ── validate_import_documents — near-empty content ────────────────────────────

def test_near_empty_content_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_IMPORT_MAX_FILE_BYTES", 100 * 1024 * 1024)
    monkeypatch.setattr(orch, "_IMPORT_MIN_CONTENT_CHARS", 20)

    empty = tmp_path / "empty.txt"
    empty.write_text("Hi", encoding="utf-8")  # 2 chars < 20

    def _mock_quality(*args, **kwargs):
        return "suspicious", "near_empty"
    monkeypatch.setattr(orch, "_llm_import_quality_check", _mock_quality)

    result = validate([empty])

    assert result.per_file[0].anomalous


# ── ArchiveImportResult.to_dict ───────────────────────────────────────────────

def test_archive_import_result_serialises(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("Enough content here for the import gate.", encoding="utf-8")

    result = validate([f])
    d = result.to_dict()

    assert "total" in d
    assert "per_file" in d
    assert isinstance(d["per_file"], list)
    assert len(d["per_file"]) == 1
    assert "accepted" in d["per_file"][0]
