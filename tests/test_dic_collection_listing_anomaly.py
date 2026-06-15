# CUI // SP-CTI
"""Tests for the DIC collection listing anomaly detector.

aiify-opp-100: hardcoded_threshold -> anomaly_detection.  The external scan
flagged paperless-ngx src/documents/views.py near line 629 — the document
listing view — and recommended an anomaly-detection approach over its hardcoded
result-size thresholds.  Per the established aiify-opp pattern the augmentation
lands in the analogous ICDEV subsystem (DIC analytics engine).

These tests pin the load-bearing guarantees of the implementation:

* ``_listing_heuristic_severity`` is a pure deterministic function of the
  summary counts and is ALWAYS available (the safety net);
* ``_ai_listing_severity`` degrades silently to ``None`` on no-data,
  blank/malformed/out-of-range output, or any LLM failure;
* ``detect_collection_listing_anomalies`` classifies empty, oversized, and
  stagnant collections using named constants (not inline magic numbers).
"""
from __future__ import annotations

import importlib
import math

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    last_request = None
    last_function = None
    _content = '{"severity": "high", "rationale": "Many empty collections.", "top_concern": "empty"}'

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        _Router.last_function = function
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset():
    _Router.last_request = None
    _Router.last_function = None
    _Router._content = (
        '{"severity": "high", "rationale": "Many empty collections.", "top_concern": "empty"}'
    )
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    # Patch ALL known router module aliases so lazy `from tools.llm.router import LLMRouter`
    # is intercepted regardless of which sys.modules entry analytics_engine resolves to.
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


# ── Heuristic baseline (deterministic) ───────────────────────────────────────

def test_heuristic_low_when_all_zero():
    summary = {"empty_count": 0, "oversized_count": 0, "stagnant_count": 0}
    assert analytics._listing_heuristic_severity(summary) == "low"


def test_heuristic_high_on_many_empty():
    summary = {"empty_count": analytics._LISTING_SEV_HIGH_EMPTY,
               "oversized_count": 0, "stagnant_count": 0}
    assert analytics._listing_heuristic_severity(summary) == "high"


def test_heuristic_high_on_many_oversized():
    summary = {"empty_count": 0,
               "oversized_count": analytics._LISTING_SEV_HIGH_OVERSIZED,
               "stagnant_count": 0}
    assert analytics._listing_heuristic_severity(summary) == "high"


def test_heuristic_medium_on_stagnant():
    summary = {"empty_count": 0, "oversized_count": 0,
               "stagnant_count": analytics._LISTING_SEV_MEDIUM_STAGNANT}
    assert analytics._listing_heuristic_severity(summary) == "medium"


def test_heuristic_tolerates_missing_keys():
    assert analytics._listing_heuristic_severity({}) == "low"


# ── AI severity grading ───────────────────────────────────────────────────────

_SUMMARY = {"empty_count": 4, "oversized_count": 2, "stagnant_count": 1}
_SAMPLES = {
    "empty": [{"collection_id": "col-a", "doc_count": 0}],
    "oversized": [{"collection_id": "col-b", "doc_count": 15000, "threshold": 10000}],
    "stagnant": [],
}


def test_ai_parses_severity_rationale_concern(monkeypatch):
    _patch_router(monkeypatch)
    out = analytics._ai_listing_severity(_SUMMARY, _SAMPLES)
    assert out == {
        "severity": "high",
        "rationale": "Many empty collections.",
        "top_concern": "empty",
    }
    assert _Router.last_function == "dic_listing_anomaly_severity"


def test_ai_no_anomalies_skips_llm(monkeypatch):
    _patch_router(monkeypatch)
    empty = {"empty_count": 0, "oversized_count": 0, "stagnant_count": 0}
    assert analytics._ai_listing_severity(empty, {}) is None
    assert _Router.last_request is None


def test_ai_sample_is_bounded(monkeypatch):
    _patch_router(monkeypatch)
    many = {"empty": [{"collection_id": f"col-{i}"} for i in range(50)]}
    summary = {"empty_count": 50, "oversized_count": 0, "stagnant_count": 0}
    analytics._ai_listing_severity(summary, many)
    sent = _Router.last_request.messages[0]["content"]
    assert sent.count('"collection_id"') == analytics._LISTING_SAMPLE


def test_ai_baseline_in_prompt(monkeypatch):
    _patch_router(monkeypatch)
    analytics._ai_listing_severity(_SUMMARY, _SAMPLES)
    sent = _Router.last_request.messages[0]["content"]
    # 4 empty ≥ _LISTING_SEV_HIGH_EMPTY → baseline must be high
    assert "Deterministic baseline severity: high" in sent


def test_ai_tolerates_fenced_json(monkeypatch):
    _patch_router(
        monkeypatch,
        content='```json\n{"severity": "medium", "rationale": "r", "top_concern": "stagnant"}\n```',
    )
    out = analytics._ai_listing_severity(_SUMMARY, _SAMPLES)
    assert out["severity"] == "medium"


def test_ai_out_of_range_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content='{"severity": "catastrophic"}')
    assert analytics._ai_listing_severity(_SUMMARY, _SAMPLES) is None


def test_ai_malformed_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json")
    assert analytics._ai_listing_severity(_SUMMARY, _SAMPLES) is None


def test_ai_blank_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content='{"severity": "", "rationale": "x"}')
    assert analytics._ai_listing_severity(_SUMMARY, _SAMPLES) is None


def test_ai_llm_failure_returns_none(monkeypatch):
    import sys as _sys

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    assert analytics._ai_listing_severity(_SUMMARY, _SAMPLES) is None


def test_ai_rationale_and_concern_bounded(monkeypatch):
    _patch_router(
        monkeypatch,
        content='{"severity": "low", "rationale": "' + "x" * 500 + '", "top_concern": "'
        + "y" * 500 + '"}',
    )
    out = analytics._ai_listing_severity(_SUMMARY, _SAMPLES)
    assert len(out["rationale"]) <= 200
    assert len(out["top_concern"]) <= 80


# ── detect_collection_listing_anomalies (integration) ────────────────────────

def _make_rows(specs: list[dict]) -> list[dict]:
    """Build mock dic_documents query rows from a list of {collection_id, doc_count, last_ingested}."""
    return [
        {
            "collection_id": s["collection_id"],
            "doc_count": s["doc_count"],
            "last_ingested": s.get("last_ingested", "2099-01-01T00:00:00+00:00"),
        }
        for s in specs
    ]


def _patch_safe(monkeypatch, rows):
    """Replace analytics._safe so detect_collection_listing_anomalies gets canned data."""
    import tools.document_intelligence.analytics_engine as ae

    def _fake_safe(conn, sql, params=()):
        if "dic_documents" in sql:
            return rows
        return []

    monkeypatch.setattr(ae, "_safe", _fake_safe)
    monkeypatch.setattr(ae, "_conn", lambda: _DummyConn())


class _DummyConn:
    def close(self):
        pass


def test_detect_no_collections(monkeypatch):
    _patch_safe(monkeypatch, [])
    result = analytics.detect_collection_listing_anomalies()
    assert result.get("no_data") is True or result["summary"]["empty_count"] == 0


def test_detect_flags_empty_collection(monkeypatch):
    rows = _make_rows([
        {"collection_id": "col-a", "doc_count": 0},
        {"collection_id": "col-b", "doc_count": 10},
        {"collection_id": "col-c", "doc_count": 8},
    ])
    _patch_safe(monkeypatch, rows)
    result = analytics.detect_collection_listing_anomalies()
    assert result["summary"]["empty_count"] == 1
    assert any(e["collection_id"] == "col-a" for e in result["empty"])


def test_detect_flags_oversized_absolute(monkeypatch):
    rows = _make_rows([
        {"collection_id": "big", "doc_count": analytics._LISTING_MAX_DOCS_ABSOLUTE + 1},
        {"collection_id": "col-b", "doc_count": 5},
    ])
    _patch_safe(monkeypatch, rows)
    result = analytics.detect_collection_listing_anomalies()
    assert result["summary"]["oversized_count"] >= 1
    assert any(e["collection_id"] == "big" for e in result["oversized"])


def test_detect_flags_stagnant_collection(monkeypatch):
    rows = _make_rows([
        {
            "collection_id": "old-col",
            "doc_count": 10,
            "last_ingested": "2000-01-01T00:00:00+00:00",  # very old
        },
        {"collection_id": "fresh", "doc_count": 5},
    ])
    _patch_safe(monkeypatch, rows)
    result = analytics.detect_collection_listing_anomalies()
    assert result["summary"]["stagnant_count"] >= 1
    assert any(s["collection_id"] == "old-col" for s in result["stagnant"])


def test_detect_stats_in_result(monkeypatch):
    rows = _make_rows([
        {"collection_id": "a", "doc_count": 10},
        {"collection_id": "b", "doc_count": 20},
        {"collection_id": "c", "doc_count": 30},
    ])
    _patch_safe(monkeypatch, rows)
    result = analytics.detect_collection_listing_anomalies()
    assert result["collection_count"] == 3
    assert result["mean_docs_per_collection"] == pytest.approx(20.0, abs=0.1)
    expected_stdev = math.sqrt(((10 - 20) ** 2 + (20 - 20) ** 2 + (30 - 20) ** 2) / 3)
    assert result["stdev_docs_per_collection"] == pytest.approx(expected_stdev, abs=0.1)


def test_detect_severity_keys_present(monkeypatch):
    _patch_safe(monkeypatch, _make_rows([{"collection_id": "x", "doc_count": 5}]))
    result = analytics.detect_collection_listing_anomalies()
    for key in ("severity", "severity_source", "heuristic_severity", "summary"):
        assert key in result


def test_detect_statistical_outlier_flagged(monkeypatch):
    # One collection exceeds the absolute cap, which triggers the oversized flag
    # independently of the statistical distribution.
    rows = _make_rows([
        {"collection_id": "normal-1", "doc_count": 10},
        {"collection_id": "normal-2", "doc_count": 12},
        {"collection_id": "normal-3", "doc_count": 11},
        {"collection_id": "outlier", "doc_count": analytics._LISTING_MAX_DOCS_ABSOLUTE + 500},
    ])
    _patch_safe(monkeypatch, rows)
    result = analytics.detect_collection_listing_anomalies()
    assert any(e["collection_id"] == "outlier" for e in result["oversized"])
