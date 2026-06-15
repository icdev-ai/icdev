"""Tests for duplex-scan artifact detection in _ai_metadata_anomaly_detection.

aiify-opp-27: hardcoded_threshold -> anomaly_detection in document ingestion.
The duplex pre-check fires when an even page count (≥ 4) coincides with a
chars-per-page average below an adaptive floor
(_ANOMALY_MIN_CHARS_PER_PAGE × _ANOMALY_DUPLEX_CPP_RATIO) rather than a single
hardcoded constant.  Both operands are env-var-backed so operators can tune
once without maintaining two independent numbers.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")

Extraction = ingest.Extraction


# ── Stub LLM ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeRouter:
    last_fn: str | None = None
    last_req = None
    _content: str = json.dumps({"score": 0.5, "verdict": "suspicious", "reason": "duplex"})

    def __init__(self, *a, **k):
        pass

    def invoke(self, fn, req):
        _FakeRouter.last_fn = fn
        _FakeRouter.last_req = req
        return _Resp(self._content)


class _BrokenRouter:
    def __init__(self, *a, **k):
        pass

    def invoke(self, fn, req):
        raise RuntimeError("LLM unavailable")


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeRouter.last_fn = None
    _FakeRouter.last_req = None
    _FakeRouter._content = json.dumps({"score": 0.5, "verdict": "suspicious", "reason": "duplex"})
    yield


def _e(text: str, page_count: int, **kw) -> Extraction:
    return Extraction(
        text=text,
        provider="test",
        content_type="application/pdf",
        page_count=page_count,
        **kw,
    )


# Default floor = _ANOMALY_MIN_CHARS_PER_PAGE (10) × _ANOMALY_DUPLEX_CPP_RATIO (3.0) = 30
_SPARSE_TEXT = "A" * 100   # cpp = 100 / 8 = 12.5, well below 30


# ── Pre-check 5 signal fires ─────────────────────────────────────────────────

def test_duplex_signal_fires_for_even_sparse_doc(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=8), "scan.pdf")
    assert result is not None
    duplex_sigs = [s for s in result["signals"] if s.startswith("possible_duplex_artifact")]
    assert len(duplex_sigs) == 1
    assert "pages=8(even)" in duplex_sigs[0]


def test_duplex_signal_contains_cpp_and_floor_in_label(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=8), "scan.pdf")
    assert result is not None
    sig = next(s for s in result["signals"] if s.startswith("possible_duplex_artifact"))
    # cpp=12.5<30 in label (floor = 10 * 3.0 = 30)
    assert "cpp=" in sig
    assert "<30" in sig


# ── Parity guard — odd page count blocks the signal ──────────────────────────

def test_duplex_signal_not_fired_for_odd_page_count():
    # 7 pages (odd) + 100 chars → cpp = 14.3 < 30, but parity guard prevents signal
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=7), "scan.pdf")
    # No signals at all: cpp > _ANOMALY_MIN_CHARS_PER_PAGE, text ≥ _ANOMALY_MIN_TEXT_LEN
    assert result is None


# ── Minimum-page guard — below 4 pages no duplex check ───────────────────────

def test_duplex_signal_not_fired_for_two_page_doc():
    # 2 pages (even, < 4) + 60 chars → cpp = 30, exactly at floor; guard skips
    result = ingest._ai_metadata_anomaly_detection(_e("A" * 60, page_count=2), "scan.pdf")
    assert result is None


def test_duplex_signal_fires_at_minimum_qualifying_page_count(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    # 4 pages (even, ≥4) + 100 chars → cpp = 25.0 < 30
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=4), "scan.pdf")
    assert result is not None
    assert any(s.startswith("possible_duplex_artifact") for s in result["signals"])


# ── Content-sufficient case — no duplex signal ───────────────────────────────

def test_duplex_signal_not_fired_when_cpp_exceeds_floor():
    # 8 pages + 400 chars → cpp = 50.0 > 30.0; no signal
    result = ingest._ai_metadata_anomaly_detection(_e("A" * 400, page_count=8), "scan.pdf")
    assert result is None


# ── Adaptive ratio — env-var controls the floor ──────────────────────────────

def test_duplex_ratio_attribute_controls_floor(monkeypatch):
    # Raise ratio to 6.0 → floor becomes 60; doc with cpp=50 now triggers
    monkeypatch.setattr(ingest, "_ANOMALY_DUPLEX_CPP_RATIO", 6.0)
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_metadata_anomaly_detection(_e("A" * 400, page_count=8), "scan.pdf")
    assert result is not None
    assert any(s.startswith("possible_duplex_artifact") for s in result["signals"])


def test_duplex_ratio_attribute_suppresses_signal(monkeypatch):
    # Lower ratio to 1.0 → floor becomes 10; doc with cpp=12.5 no longer triggers
    monkeypatch.setattr(ingest, "_ANOMALY_DUPLEX_CPP_RATIO", 1.0)
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=8), "scan.pdf")
    # cpp (12.5) > floor (10 * 1.0 = 10); no other signals fire either
    assert result is None


# ── LLM integration ───────────────────────────────────────────────────────────

def test_duplex_signal_triggers_llm_call(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=8), "duplex_scan.pdf")
    assert _FakeRouter.last_fn == "anomaly_detection"


def test_duplex_result_has_required_keys(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=8), "scan.pdf")
    assert result is not None
    for key in ("score", "verdict", "reason", "signals"):
        assert key in result, f"missing key: {key}"


def test_duplex_signal_degrades_gracefully_on_llm_failure(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _BrokenRouter)
    result = ingest._ai_metadata_anomaly_detection(_e(_SPARSE_TEXT, page_count=8), "scan.pdf")
    assert result is not None
    assert result["score"] == 0.5
    assert result["verdict"] == "suspicious"
    assert result["reason"] == "llm_unavailable"
    assert any(s.startswith("possible_duplex_artifact") for s in result["signals"])
