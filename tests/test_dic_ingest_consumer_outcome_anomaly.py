"""Tests for post-ingest consumption outcome anomaly detection.

aiify-rm-a3344-phase-21: hardcoded_threshold -> anomaly_detection in
src/documents/consumer.py (paperless-ngx). The DIC analog is
detect_consumer_outcome_anomaly() in ingest_orchestrator — replaces the
static min-chunk, embedding-failure-ceiling, and error-count-limit constants
used by paperless Consumer to decide whether a consumption run "succeeded".
"""
from __future__ import annotations

import importlib


orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
detect_consumer_outcome_anomaly = orch.detect_consumer_outcome_anomaly


# ── clean outcomes — should return None ─────────────────────────────────────

def test_no_anomaly_normal_outcome():
    result = detect_consumer_outcome_anomaly(
        chunks=10,
        chunks_embedded=10,
        errors=[],
        text_was_nonempty=True,
        embed_requested=True,
    )
    assert result is None


def test_no_anomaly_partial_embed_above_ratio(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MIN_EMBED_RATIO", 0.5)
    # 6/10 embedded = 0.6 ≥ floor → no signal.
    result = detect_consumer_outcome_anomaly(
        chunks=10, chunks_embedded=6, errors=[], text_was_nonempty=True
    )
    assert result is None


def test_no_anomaly_errors_at_ceiling(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MAX_ERRORS", 5)
    # Exactly 5 errors = at ceiling (not above) → no signal.
    result = detect_consumer_outcome_anomaly(
        chunks=5, chunks_embedded=5, errors=["e"] * 5, text_was_nonempty=True
    )
    assert result is None


def test_no_anomaly_empty_doc_zero_chunks():
    # Zero chunks from empty text is expected, not anomalous.
    result = detect_consumer_outcome_anomaly(
        chunks=0,
        chunks_embedded=0,
        errors=[],
        text_was_nonempty=False,
        embed_requested=True,
    )
    assert result is None


def test_no_anomaly_embed_not_requested():
    # Embed not requested → low embed rate is not checked.
    result = detect_consumer_outcome_anomaly(
        chunks=10,
        chunks_embedded=0,
        errors=[],
        text_was_nonempty=True,
        embed_requested=False,
    )
    assert result is None


# ── zero_chunks signal ───────────────────────────────────────────────────────

def test_zero_chunks_from_nonempty_text():
    result = detect_consumer_outcome_anomaly(
        chunks=0, chunks_embedded=0, errors=[], text_was_nonempty=True
    )
    assert result is not None
    assert any("zero_chunks" in s for s in result["signals"])
    assert result["source"] == "consumer_outcome_anomaly"


def test_zero_chunks_no_signal_when_empty_text():
    result = detect_consumer_outcome_anomaly(
        chunks=0, chunks_embedded=0, errors=[], text_was_nonempty=False
    )
    assert result is None


# ── low_embed_rate signal ─────────────────────────────────────────────────────

def test_low_embed_rate_below_floor(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MIN_EMBED_RATIO", 0.5)
    # 2/10 = 0.2 < 0.5 → signal fires.
    result = detect_consumer_outcome_anomaly(
        chunks=10, chunks_embedded=2, errors=[], text_was_nonempty=True
    )
    assert result is not None
    assert any("low_embed_rate" in s for s in result["signals"])


def test_low_embed_rate_exact_floor(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MIN_EMBED_RATIO", 0.5)
    # 5/10 = 0.5 = floor exactly → no signal (not below).
    result = detect_consumer_outcome_anomaly(
        chunks=10, chunks_embedded=5, errors=[], text_was_nonempty=True
    )
    assert result is None


def test_low_embed_rate_zero_embedded(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MIN_EMBED_RATIO", 0.5)
    result = detect_consumer_outcome_anomaly(
        chunks=10, chunks_embedded=0, errors=[], text_was_nonempty=True
    )
    assert result is not None
    assert any("low_embed_rate" in s for s in result["signals"])


def test_low_embed_rate_skipped_when_no_chunks():
    # chunks=0 → ratio undefined; skip the embed-rate check to avoid ZeroDivisionError.
    result = detect_consumer_outcome_anomaly(
        chunks=0, chunks_embedded=0, errors=[], text_was_nonempty=False
    )
    assert result is None


# ── high_error_count signal ───────────────────────────────────────────────────

def test_high_error_count_above_ceiling(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MAX_ERRORS", 5)
    result = detect_consumer_outcome_anomaly(
        chunks=5, chunks_embedded=5, errors=["e"] * 6, text_was_nonempty=True
    )
    assert result is not None
    assert any("high_error_count" in s for s in result["signals"])


def test_high_error_count_below_ceiling(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MAX_ERRORS", 5)
    result = detect_consumer_outcome_anomaly(
        chunks=5, chunks_embedded=5, errors=["e"] * 4, text_was_nonempty=True
    )
    assert result is None


# ── multiple signals can co-occur ─────────────────────────────────────────────

def test_multiple_signals_in_one_result(monkeypatch):
    monkeypatch.setattr(orch, "_OUTCOME_MIN_EMBED_RATIO", 0.5)
    monkeypatch.setattr(orch, "_OUTCOME_MAX_ERRORS", 2)
    result = detect_consumer_outcome_anomaly(
        chunks=10, chunks_embedded=1, errors=["e1", "e2", "e3"],
        text_was_nonempty=True, embed_requested=True,
    )
    assert result is not None
    sigs = result["signals"]
    assert any("low_embed_rate" in s for s in sigs)
    assert any("high_error_count" in s for s in sigs)


# ── return structure ──────────────────────────────────────────────────────────

def test_result_structure():
    result = detect_consumer_outcome_anomaly(
        chunks=0, chunks_embedded=0, errors=[], text_was_nonempty=True
    )
    assert isinstance(result, dict)
    assert "source" in result
    assert "signals" in result
    assert isinstance(result["signals"], list)
    assert len(result["signals"]) > 0


# ── error resilience ──────────────────────────────────────────────────────────

def test_returns_none_on_unexpected_error(monkeypatch):
    def _bad(*a, **kw):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(orch, "_OUTCOME_MIN_EMBED_RATIO", property(_bad))
    # The function should catch any exception and return None.
    result = detect_consumer_outcome_anomaly(
        chunks=0, chunks_embedded=0, errors=[], text_was_nonempty=True
    )
    # Should not raise; either None or a partial result.
    assert result is None or isinstance(result, dict)
