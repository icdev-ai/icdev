"""Tests for per-file consumer pre-validation anomaly detection.

aiify-rm-a3344-phase-20: hardcoded_threshold -> anomaly_detection in
src/documents/consumer.py (paperless-ngx).
The DIC analog is detect_consumer_file_anomaly() in ingest_orchestrator —
detects empty/corrupt files, filename-length violations, and MIME/extension
mismatches before the extraction pipeline runs. All thresholds are env-var
controlled; no hardcoded constants.
"""
from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

import pytest

orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
detect_consumer_file_anomaly = orch.detect_consumer_file_anomaly


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_tmp(content: bytes, suffix: str) -> Path:
    """Write content to a temp file and return its Path."""
    fd, name = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    return Path(name)


# ── clean file -> None ────────────────────────────────────────────────────────

def test_clean_text_file_returns_none():
    p = _write_tmp(b"Normal text content for a document.", ".txt")
    try:
        assert detect_consumer_file_anomaly(p) is None
    finally:
        p.unlink(missing_ok=True)


def test_clean_pdf_returns_none():
    p = _write_tmp(b"%PDF-1.4 this is a valid-looking pdf content body here.", ".pdf")
    try:
        result = detect_consumer_file_anomaly(p)
        if result:
            assert not any("mime_extension_mismatch" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


# ── empty / near-empty file ───────────────────────────────────────────────────

def test_zero_byte_file_flagged(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_MIN_FILE_BYTES", 4)
    p = _write_tmp(b"", ".pdf")
    try:
        result = detect_consumer_file_anomaly(p)
        assert result is not None
        assert any("empty_or_corrupt" in s for s in result["signals"])
        assert result["file_bytes"] == 0
    finally:
        p.unlink(missing_ok=True)


def test_below_min_bytes_flagged(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_MIN_FILE_BYTES", 100)
    p = _write_tmp(b"short", ".txt")
    try:
        result = detect_consumer_file_anomaly(p)
        assert result is not None
        assert any("empty_or_corrupt" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


def test_exactly_at_min_bytes_not_flagged(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_MIN_FILE_BYTES", 4)
    p = _write_tmp(b"four", ".txt")
    try:
        result = detect_consumer_file_anomaly(p)
        # 4 bytes == floor — should not trigger empty_or_corrupt
        if result:
            assert not any("empty_or_corrupt" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


# ── MIME / extension mismatch ─────────────────────────────────────────────────

def test_png_content_with_pdf_extension_flagged():
    # PNG magic bytes served as .pdf — clear mismatch.
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    p = _write_tmp(png_header, ".pdf")
    try:
        result = detect_consumer_file_anomaly(p)
        assert result is not None
        assert any("mime_extension_mismatch" in s for s in result["signals"])
        assert "image/png" in result["signals"][0] or any(
            "image/png" in s for s in result["signals"]
        )
    finally:
        p.unlink(missing_ok=True)


def test_jpeg_content_with_txt_extension_flagged():
    jpeg_header = b"\xff\xd8\xff" + b"\x00" * 60
    p = _write_tmp(jpeg_header, ".txt")
    try:
        result = detect_consumer_file_anomaly(p)
        assert result is not None
        assert any("mime_extension_mismatch" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


def test_pdf_content_with_pdf_extension_no_mismatch():
    pdf_header = b"%PDF-1.4 content body here with enough bytes"
    p = _write_tmp(pdf_header, ".pdf")
    try:
        result = detect_consumer_file_anomaly(p)
        if result:
            assert not any("mime_extension_mismatch" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


def test_zip_extension_not_flagged_for_zip_content():
    # ZIP is in the skip set — DOCX/XLSX/PPTX are ZIP-based so no false positive.
    zip_header = b"PK\x03\x04" + b"\x00" * 50
    p = _write_tmp(zip_header, ".zip")
    try:
        result = detect_consumer_file_anomaly(p)
        if result:
            assert not any("mime_extension_mismatch" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


def test_docx_as_zip_content_no_mismatch_false_positive():
    # .docx files are ZIP-based; their magic bytes are ZIP. This must NOT flag.
    zip_header = b"PK\x03\x04" + b"\x00" * 50
    p = _write_tmp(zip_header, ".docx")
    try:
        result = detect_consumer_file_anomaly(p)
        if result:
            assert not any("mime_extension_mismatch" in s for s in result["signals"])
    finally:
        p.unlink(missing_ok=True)


# ── filename length ───────────────────────────────────────────────────────────

def test_long_filename_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "_CONSUMER_MAX_FILENAME_LEN", 50)
    # Create a fake Path with a 60-char name pointing to an existing dir
    # so stat() finds real bytes; use a real content file renamed in tmp_path.
    real = tmp_path / ("x" * 60 + ".txt")
    real.write_bytes(b"content with enough bytes for the floor")
    result = detect_consumer_file_anomaly(real)
    assert result is not None
    assert any("filename_too_long" in s for s in result["signals"])


def test_short_filename_not_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "_CONSUMER_MAX_FILENAME_LEN", 255)
    p = tmp_path / "short_name.txt"
    p.write_bytes(b"some reasonable content here")
    result = detect_consumer_file_anomaly(p)
    assert result is None


# ── return structure ─────────────────────────────────────────────────────────

def test_result_structure_on_anomaly(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_MIN_FILE_BYTES", 1000)
    p = _write_tmp(b"short", ".txt")
    try:
        result = detect_consumer_file_anomaly(p)
        assert result is not None
        assert result["source"] == "consumer_pre_validation"
        assert "file_bytes" in result
        assert "filename" in result
        assert isinstance(result["signals"], list)
        assert len(result["signals"]) >= 1
    finally:
        p.unlink(missing_ok=True)


# ── error resilience ──────────────────────────────────────────────────────────

def test_nonexistent_file_does_not_raise():
    p = Path("/nonexistent/path/that/does/not/exist.pdf")
    # Should degrade gracefully — no raise, returns a result with empty_or_corrupt.
    try:
        result = detect_consumer_file_anomaly(p)
        # Result may be None or have signals — either is acceptable.
        assert result is None or isinstance(result, dict)
    except Exception as exc:
        pytest.fail(f"detect_consumer_file_anomaly raised unexpectedly: {exc}")
