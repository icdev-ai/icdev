"""Tests for LLM-assisted filter query parsing in the DIC search engine.

aiify-opp-29: fulltext_search_engine -> llm_generation, analog of the
paperless src/documents/filters.py document filter module. ``DICSearchEngine.
filter_query`` converts natural language filter intent (e.g. "recent CUI PDFs
from last 30 days") into structured filter parameters without requiring callers
to know field names. These tests pin its load-bearing guarantees:

* valid filters are extracted and schema-validated (classification, content_type,
  date fields, title_contains, page range, collection_id);
* invalid / invented field values are silently dropped (never passed to SQL);
* it always degrades gracefully — blank query, LLM failure, blank output, invalid
  JSON, empty JSON, and no-applicable-filter all return a usable DICFilterQuery;
* code fences in LLM output are stripped before JSON parsing;
* ``filtered_search`` post-filters results to doc_ids matching the filters, and
  falls back to unrestricted search when filters are empty.
"""
from __future__ import annotations

import importlib
import json

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that returns a canned JSON filter object."""

    last_request = None
    _content = json.dumps({
        "classification": "CUI",
        "content_type": "pdf",
        "date_range_days": 30,
        "confidence": 0.9,
    })

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = json.dumps({
        "classification": "CUI",
        "content_type": "pdf",
        "date_range_days": 30,
        "confidence": 0.9,
    })
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


# --------------------------------------------------------------------------- #
# Empty query — refuse without calling the LLM
# --------------------------------------------------------------------------- #

def test_empty_query_refuses_without_llm(monkeypatch):
    _patch_router(monkeypatch)
    fq = se.DICSearchEngine().filter_query("   ")
    assert fq.llm_used is False
    assert fq.refusal_reason == "empty_query"
    assert fq.filters == {}
    assert _Router.last_request is None


# --------------------------------------------------------------------------- #
# Happy path: valid JSON with known filters
# --------------------------------------------------------------------------- #

def test_valid_filters_extracted(monkeypatch):
    _patch_router(monkeypatch)
    fq = se.DICSearchEngine().filter_query("recent CUI PDFs from last 30 days")
    assert fq.llm_used is True
    assert fq.refusal_reason == ""
    assert fq.filters["classification"] == "CUI"
    assert fq.filters["content_type"] == "pdf"
    assert fq.filters["date_range_days"] == 30
    assert fq.confidence == pytest.approx(0.9)
    assert fq.origin == "ai_generated"


def test_to_dict_shape(monkeypatch):
    _patch_router(monkeypatch)
    d = se.DICSearchEngine().filter_query("recent CUI PDFs").to_dict()
    assert set(d) == {"natural_query", "filters", "confidence", "llm_used", "refusal_reason", "origin"}
    assert isinstance(d["filters"], dict)


def test_all_valid_filter_keys_extracted(monkeypatch):
    payload = json.dumps({
        "classification": "SECRET",
        "content_type": "docx",
        "date_after": "2025-01-01",
        "date_before": "2025-12-31",
        "title_contains": "contract",
        "collection_id": "legal-docs",
        "min_pages": 5,
        "max_pages": 100,
        "confidence": 0.85,
    })
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("5-100 page SECRET DOCX contracts from 2025")
    assert fq.filters["classification"] == "SECRET"
    assert fq.filters["content_type"] == "docx"
    assert fq.filters["date_after"] == "2025-01-01"
    assert fq.filters["date_before"] == "2025-12-31"
    assert fq.filters["title_contains"] == "contract"
    assert fq.filters["collection_id"] == "legal-docs"
    assert fq.filters["min_pages"] == 5
    assert fq.filters["max_pages"] == 100


# --------------------------------------------------------------------------- #
# Schema validation: invalid values are dropped silently
# --------------------------------------------------------------------------- #

def test_invalid_classification_dropped(monkeypatch):
    payload = json.dumps({"classification": "TOPSECRETSPECIALACCESS", "content_type": "pdf"})
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("some docs")
    assert "classification" not in fq.filters
    assert fq.filters["content_type"] == "pdf"


def test_invalid_content_type_dropped(monkeypatch):
    payload = json.dumps({"content_type": "exe", "classification": "CUI"})
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("some docs")
    assert "content_type" not in fq.filters
    assert fq.filters["classification"] == "CUI"


def test_invalid_date_format_dropped(monkeypatch):
    payload = json.dumps({"date_after": "January 2025", "date_range_days": 30})
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("recent docs")
    assert "date_after" not in fq.filters
    assert fq.filters["date_range_days"] == 30


def test_date_range_out_of_bounds_dropped(monkeypatch):
    payload = json.dumps({"date_range_days": 99999, "content_type": "pdf"})
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("very old docs")
    assert "date_range_days" not in fq.filters
    assert fq.filters["content_type"] == "pdf"


def test_unknown_keys_stripped(monkeypatch):
    payload = json.dumps({
        "classification": "CUI",
        "invented_key": "something malicious",
        "another_unknown": 42,
    })
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("some docs")
    assert "invented_key" not in fq.filters
    assert "another_unknown" not in fq.filters
    assert fq.filters["classification"] == "CUI"


def test_overlong_title_contains_dropped(monkeypatch):
    long_title = "x" * 200
    payload = json.dumps({"title_contains": long_title, "classification": "CUI"})
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("very long title filter")
    assert "title_contains" not in fq.filters
    assert fq.filters.get("classification") == "CUI"


# --------------------------------------------------------------------------- #
# Code fence stripping
# --------------------------------------------------------------------------- #

def test_code_fence_stripped_before_parse(monkeypatch):
    payload = "```json\n" + json.dumps({"classification": "CUI", "confidence": 0.8}) + "\n```"
    _patch_router(monkeypatch, content=payload)
    fq = se.DICSearchEngine().filter_query("CUI docs")
    assert fq.filters["classification"] == "CUI"
    assert fq.llm_used is True


# --------------------------------------------------------------------------- #
# Degradation never throws — always returns a usable DICFilterQuery
# --------------------------------------------------------------------------- #

def test_empty_json_returns_no_filters(monkeypatch):
    _patch_router(monkeypatch, content="{}")
    fq = se.DICSearchEngine().filter_query("documents")
    assert fq.llm_used is True
    assert fq.refusal_reason == "no_filters"
    assert fq.filters == {}


def test_invalid_json_returns_no_filters(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    fq = se.DICSearchEngine().filter_query("documents")
    assert fq.llm_used is True
    assert fq.refusal_reason == "no_filters"
    assert fq.filters == {}


def test_blank_response_returns_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content="   ")
    fq = se.DICSearchEngine().filter_query("documents")
    assert fq.llm_used is False
    assert fq.refusal_reason == "llm_unavailable"
    assert fq.filters == {}


def test_none_response_returns_llm_unavailable(monkeypatch):
    class _NoneRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            return None

    monkeypatch.setattr(router_mod, "LLMRouter", _NoneRouter)
    import sys as _sys
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _NoneRouter)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _NoneRouter)
    fq = se.DICSearchEngine().filter_query("recent CUI docs")
    assert fq.llm_used is False
    assert fq.refusal_reason == "llm_unavailable"
    assert fq.filters == {}


def test_llm_exception_returns_llm_unavailable(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import sys as _sys
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    fq = se.DICSearchEngine().filter_query("recent classified docs")
    assert fq.llm_used is False
    assert fq.refusal_reason == "llm_unavailable"
    assert fq.filters == {}


# --------------------------------------------------------------------------- #
# filtered_search: post-filtering by doc_id
# --------------------------------------------------------------------------- #

def _make_result(doc_id: str, score: float = 0.5) -> "se.DICSearchResult":
    r = se.DICSearchResult(
        chunk_id=f"{doc_id}-chunk-0",
        doc_id=doc_id,
        doc_title=f"Doc {doc_id}",
        content="test content for search",
        score=score,
        citation=se.Citation(doc_id=doc_id, doc_title=f"Doc {doc_id}"),
    )
    r.attribution_score = score
    return r


def test_filtered_search_with_empty_filters_delegates_to_search(monkeypatch):
    """Empty filters → no doc restriction → returns whatever search() returns."""
    expected = [_make_result("doc-a"), _make_result("doc-b")]

    def _fake_search(self, query, collection_id=None, top_k=10, mode="grounded", clearance=None, rerank_attribution=True):
        return expected

    monkeypatch.setattr(se.DICSearchEngine, "search", _fake_search)
    results = se.DICSearchEngine().filtered_search("contracts", filters={}, top_k=10)
    assert results == expected


def test_filtered_search_with_none_filters_delegates_to_search(monkeypatch):
    expected = [_make_result("doc-x")]

    def _fake_search(self, query, collection_id=None, top_k=10, mode="grounded", clearance=None, rerank_attribution=True):
        return expected

    monkeypatch.setattr(se.DICSearchEngine, "search", _fake_search)
    results = se.DICSearchEngine().filtered_search("test", filters=None)
    assert results == expected


def _patch_get_connection(monkeypatch, conn_factory):
    """Shim-aware patching of get_connection across all module aliases."""
    import sys as _sys
    import importlib as _il
    _stor = _il.import_module("tools.db.storage")
    monkeypatch.setattr(_stor, "get_connection", conn_factory)
    for _key, _mod in list(_sys.modules.items()):
        if "db.storage" in _key and hasattr(_mod, "get_connection"):
            monkeypatch.setattr(_mod, "get_connection", conn_factory)


def test_filtered_search_restricts_to_allowed_doc_ids(monkeypatch):
    """When filter DB query returns allowed_doc_ids, only matching results survive."""
    all_results = [
        _make_result("doc-allowed", score=0.9),
        _make_result("doc-restricted", score=0.8),
        _make_result("doc-allowed-2", score=0.7),
    ]

    def _fake_search(self, query, collection_id=None, top_k=10, mode="grounded", clearance=None, rerank_attribution=True):
        return all_results

    class _FakeCursor:
        def fetchall(self):
            return [("doc-allowed",), ("doc-allowed-2",)]

    class _FakeConn:
        def execute(self, sql, params):
            return _FakeCursor()

        def close(self):
            pass

    _patch_get_connection(monkeypatch, lambda: _FakeConn())
    monkeypatch.setattr(se.DICSearchEngine, "search", _fake_search)

    fq = se.DICFilterQuery(
        natural_query="recent CUI docs",
        filters={"classification": "CUI"},
        llm_used=True,
        confidence=0.9,
    )
    results = se.DICSearchEngine().filtered_search("contracts", filters=fq, top_k=10)
    doc_ids = {r.doc_id for r in results}
    assert "doc-allowed" in doc_ids
    assert "doc-allowed-2" in doc_ids
    assert "doc-restricted" not in doc_ids


def test_filtered_search_empty_allowed_set_returns_empty(monkeypatch):
    """Filter matches zero documents → return empty immediately (no search run)."""
    search_called = []

    def _fake_search(self, *a, **k):
        search_called.append(True)
        return []

    class _FakeCursor:
        def fetchall(self):
            return []  # no matching docs

    class _FakeConn:
        def execute(self, sql, params):
            return _FakeCursor()

        def close(self):
            pass

    _patch_get_connection(monkeypatch, lambda: _FakeConn())
    monkeypatch.setattr(se.DICSearchEngine, "search", _fake_search)

    results = se.DICSearchEngine().filtered_search(
        "anything",
        filters={"classification": "TOP SECRET"},
    )
    assert results == []


def test_filtered_search_top_k_respected(monkeypatch):
    all_results = [_make_result(f"doc-{i}", score=1.0 - i * 0.1) for i in range(20)]
    allowed = {r.doc_id for r in all_results}

    def _fake_search(self, query, collection_id=None, top_k=10, mode="grounded", clearance=None, rerank_attribution=True):
        return all_results[:top_k]

    class _FakeCursor:
        def fetchall(self):
            return [(d,) for d in allowed]

    class _FakeConn:
        def execute(self, sql, params):
            return _FakeCursor()

        def close(self):
            pass

    _patch_get_connection(monkeypatch, lambda: _FakeConn())
    monkeypatch.setattr(se.DICSearchEngine, "search", _fake_search)

    results = se.DICSearchEngine().filtered_search("test", filters={"classification": "CUI"}, top_k=3)
    assert len(results) <= 3


def test_filtered_search_db_error_falls_back_to_unrestricted(monkeypatch):
    """DB error during filter resolution → ignore filters, run full search."""
    expected = [_make_result("doc-a")]

    def _fake_search(self, query, collection_id=None, top_k=10, mode="grounded", clearance=None, rerank_attribution=True):
        return expected

    class _BoomConn:
        def execute(self, sql, params):
            raise RuntimeError("db error")

        def close(self):
            pass

    _patch_get_connection(monkeypatch, lambda: _BoomConn())
    monkeypatch.setattr(se.DICSearchEngine, "search", _fake_search)

    results = se.DICSearchEngine().filtered_search("test", filters={"classification": "CUI"})
    assert results == expected
