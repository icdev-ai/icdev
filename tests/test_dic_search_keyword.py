"""Tests for embedding-based keyword-list search in the DIC search engine.

aiify-opp-6091 (also covers the duplicate aiify-opp-6064, the serialisers.py
sibling of the same paperless-ngx src/documents keyword filter — the single
``keyword_search`` capability below, with ``DICKeywordSearchResult.to_dict()``
serialization, covers both): keyword_list_search -> embedding_search. A classic
keyword-list filter matches a document only when it contains one of an exact list
of keywords.
``DICSearchEngine.keyword_search`` upgrades this to semantic embedding search: the
keywords are embedded and matched against chunk embeddings, so a document is
retrieved when it is semantically related to the keywords even without the literal
term. These tests pin its load-bearing guarantees:

* keyword lists are normalized (trimmed, de-duplicated, order-preserved);
* an empty / all-blank list refuses ("no_keywords") WITHOUT running a search;
* when an embedding provider is present it searches in "hybrid" (vector) mode and
  reports ``embedding_used=True``;
* when no embedding provider exists it degrades to literal keyword matching
  ("grounded" mode) and reports ``embedding_used=False`` — never worse than the
  keyword-list baseline;
* the caller's clearance is threaded through to :meth:`search` unchanged;
* results carry citations and the to_dict() shape is stable.
"""
from __future__ import annotations

import importlib

se = importlib.import_module("tools.document_intelligence.search_engine")


def _result(i, content="grounded source text"):
    return se.DICSearchResult(
        chunk_id=f"chunk-{i}",
        doc_id=f"doc-{i}",
        doc_title=f"Doc {i}",
        content=content,
        score=1.0 - i * 0.1,
        citation=se.Citation(doc_id=f"doc-{i}", doc_title=f"Doc {i}", chunk_id=f"chunk-{i}"),
    )


def _capture_search(monkeypatch, results):
    """Patch DICSearchEngine.search to record its call args and return ``results``."""
    calls = {}

    def fake_search(self, query, collection_id=None, top_k=10, mode="grounded", clearance=None):
        calls["query"] = query
        calls["collection_id"] = collection_id
        calls["top_k"] = top_k
        calls["mode"] = mode
        calls["clearance"] = clearance
        return list(results)

    monkeypatch.setattr(se.DICSearchEngine, "search", fake_search)
    return calls


def _patch_embeddings(monkeypatch, available):
    monkeypatch.setattr(
        se.DICSearchEngine, "_embeddings_available", lambda self: available
    )


# --------------------------------------------------------------------------- #
# Keyword normalization
# --------------------------------------------------------------------------- #

def test_normalize_keywords_trims_dedupes_preserves_order():
    assert se._normalize_keywords([" Budget ", "budget", "", "  ", "Cost"]) == ["Budget", "Cost"]


def test_normalize_keywords_handles_none():
    assert se._normalize_keywords(None) == []


# --------------------------------------------------------------------------- #
# Empty-list refusal (no search performed)
# --------------------------------------------------------------------------- #

def test_empty_keywords_refuses_without_search(monkeypatch):
    calls = _capture_search(monkeypatch, [_result(1)])
    _patch_embeddings(monkeypatch, True)
    res = se.DICSearchEngine().keyword_search(["", "  "])
    assert res.refusal_reason == "no_keywords"
    assert res.results == []
    assert res.result_count == 0
    assert res.embedding_used is False
    # search must not run when there are no usable keywords.
    assert calls == {}


# --------------------------------------------------------------------------- #
# Embedding path
# --------------------------------------------------------------------------- #

def test_embedding_available_uses_hybrid_mode(monkeypatch):
    calls = _capture_search(monkeypatch, [_result(1), _result(2)])
    _patch_embeddings(monkeypatch, True)
    res = se.DICSearchEngine().keyword_search(["vehicle", "transport"])
    assert res.embedding_used is True
    assert calls["mode"] == "hybrid"
    # keywords are joined into a single embedding query.
    assert calls["query"] == "vehicle transport"
    assert res.result_count == 2
    assert len(res.results) == 2
    assert res.keywords == ["vehicle", "transport"]
    assert res.refusal_reason == ""


# --------------------------------------------------------------------------- #
# Air-gap fallback path
# --------------------------------------------------------------------------- #

def test_no_embedding_degrades_to_grounded_keyword_matching(monkeypatch):
    calls = _capture_search(monkeypatch, [_result(1)])
    _patch_embeddings(monkeypatch, False)
    res = se.DICSearchEngine().keyword_search(["vehicle"])
    assert res.embedding_used is False
    assert calls["mode"] == "grounded"
    assert res.result_count == 1


# --------------------------------------------------------------------------- #
# No matches
# --------------------------------------------------------------------------- #

def test_no_matches_reports_refusal(monkeypatch):
    _capture_search(monkeypatch, [])
    _patch_embeddings(monkeypatch, True)
    res = se.DICSearchEngine().keyword_search(["nonexistent"])
    assert res.refusal_reason == "no_matches"
    assert res.results == []
    assert res.result_count == 0
    # embedding_used still reflects that the embedding path was attempted.
    assert res.embedding_used is True


# --------------------------------------------------------------------------- #
# Clearance + collection threading
# --------------------------------------------------------------------------- #

def test_clearance_and_collection_threaded_to_search(monkeypatch):
    calls = _capture_search(monkeypatch, [_result(1)])
    _patch_embeddings(monkeypatch, True)
    se.DICSearchEngine().keyword_search(
        ["budget"], collection_id="col-7", top_k=5, clearance="CUI",
    )
    assert calls["collection_id"] == "col-7"
    assert calls["top_k"] == 5
    assert calls["clearance"] == "CUI"


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #

def test_to_dict_shape(monkeypatch):
    _capture_search(monkeypatch, [_result(1)])
    _patch_embeddings(monkeypatch, True)
    d = se.DICSearchEngine().keyword_search(["budget"]).to_dict()
    assert set(d) == {
        "keywords",
        "results",
        "embedding_used",
        "result_count",
        "refusal_reason",
        "origin",
    }
    assert isinstance(d["results"], list)
    assert d["results"][0]["doc_id"] == "doc-1"
    assert d["origin"] == "ai_retrieved"
