"""Tests for citation-quality / attribution-lens reranking in the DIC search engine.

dic-adapt-04: DIC mandates citations but raw retrieval score only measures topical
match, not whether a cited chunk actually *supports* the query. Adapting the arXiv
"Re-Ranking Through an Attribution Lens for Citation Quality in Legal QA" + EviRank
work, the engine now:

* scores every result's attribution strength (query-vs-chunk evidence overlap,
  reusing the verifier's per-claim ``_lexical_overlap``);
* reranks the candidate set so a strongly-supporting chunk outranks a weakly-
  related one for the same query — even when the weak chunk retrieved with a
  higher raw score;
* exposes a per-answer citation-quality / sufficiency score on both grounded
  search (``grounded_search``) and AI-assisted generation (``answer``);
* keeps citations mandatory — every returned result still carries its citation.
"""
from __future__ import annotations

import importlib

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Raw:
    """A raw retriever hit with controllable content and retrieval score."""

    def __init__(self, i, content, score):
        self.chunk_id = f"chunk-{i}"
        self.source_id = f"doc-{i}"
        self.content = content
        self.final_score = score
        self.score = score


def _patch_pipeline(monkeypatch, hits):
    """Drive search() with controlled (content, retrieval_score) pairs.

    ``hits`` is a list of (content, score); index i maps to chunk-i / doc-i.
    No real DB or vector store is touched.
    """
    storage = importlib.import_module("tools.db.storage")

    class _Conn:
        def execute(self, *a, **k):
            return self

        def fetchone(self):
            return None

        def close(self):
            pass

    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn())
    monkeypatch.setattr(
        se.DICSearchEngine, "_rag_search",
        lambda self, query, top_k, mode, collection_id=None: [
            _Raw(i, content, score) for i, (content, score) in enumerate(hits)
        ],
    )
    monkeypatch.setattr(
        se, "_chunk_meta",
        lambda conn, chunk_id: {
            "page": 0, "section": "", "doc_id": chunk_id.replace("chunk-", "doc-"),
            "collection_id": "", "sha256": "", "attribution_pct": 0,
        },
    )
    monkeypatch.setattr(
        se, "_doc_meta",
        lambda conn, doc_id: {"title": doc_id, "classification": "CUI"},
    )


_QUERY = "annual budget increase forecast"
_STRONG = "The annual budget increase forecast projects steady growth next year."
_WEAK = "A weather report describing rain and wind over the coastal region."


# --------------------------------------------------------------------------- #
# Attribution scoring
# --------------------------------------------------------------------------- #

def test_attribution_strength_strong_beats_weak():
    strong = se._attribution_strength(_QUERY, _STRONG)
    weak = se._attribution_strength(_QUERY, _WEAK)
    assert 0.0 <= weak < strong <= 1.0


def test_attribution_score_populated_on_results(monkeypatch):
    _patch_pipeline(monkeypatch, [(_STRONG, 0.5)])
    results = se.DICSearchEngine().search(_QUERY)
    assert results[0].attribution_score > 0.0


# --------------------------------------------------------------------------- #
# Attribution-lens reranking
# --------------------------------------------------------------------------- #

def test_strong_support_outranks_weak_despite_higher_retrieval(monkeypatch):
    # Weak chunk retrieves FIRST with a HIGHER raw score; the strongly-supporting
    # chunk must still rank above it once viewed through the attribution lens.
    _patch_pipeline(monkeypatch, [(_WEAK, 0.95), (_STRONG, 0.40)])
    results = se.DICSearchEngine().search(_QUERY, top_k=10)
    assert results[0].content == _STRONG
    assert results[1].content == _WEAK
    assert results[0].attribution_score > results[1].attribution_score


def test_rerank_can_be_disabled(monkeypatch):
    # With rerank off, raw retrieval order is preserved (weak-but-higher first),
    # but attribution_score is still computed.
    _patch_pipeline(monkeypatch, [(_WEAK, 0.95), (_STRONG, 0.40)])
    results = se.DICSearchEngine().search(_QUERY, top_k=10, rerank_attribution=False)
    assert results[0].content == _WEAK
    assert results[1].content == _STRONG
    assert results[1].attribution_score > results[0].attribution_score


def test_rerank_happens_before_top_k_cap(monkeypatch):
    # The strong chunk retrieves LAST yet must survive a top_k=1 cap because the
    # attribution rerank runs over the full candidate pool before the cut.
    _patch_pipeline(monkeypatch, [(_WEAK, 0.95), (_WEAK, 0.90), (_STRONG, 0.10)])
    results = se.DICSearchEngine().search(_QUERY, top_k=1)
    assert len(results) == 1
    assert results[0].content == _STRONG


# --------------------------------------------------------------------------- #
# Per-answer citation-quality / sufficiency score — grounded search
# --------------------------------------------------------------------------- #

def test_grounded_search_returns_citation_quality(monkeypatch):
    _patch_pipeline(monkeypatch, [(_STRONG, 0.5), (_WEAK, 0.5)])
    gs = se.DICSearchEngine().grounded_search(_QUERY, top_k=10)
    assert 0.0 < gs.citation_quality <= 1.0
    assert gs.result_count == 2
    # Ordering reflects attribution strength.
    assert gs.results[0].content == _STRONG


def test_grounded_search_quality_higher_for_better_evidence(monkeypatch):
    _patch_pipeline(monkeypatch, [(_STRONG, 0.5)])
    strong_only = se.DICSearchEngine().grounded_search(_QUERY).citation_quality
    _patch_pipeline(monkeypatch, [(_WEAK, 0.5)])
    weak_only = se.DICSearchEngine().grounded_search(_QUERY).citation_quality
    assert strong_only > weak_only


def test_grounded_search_empty_quality_zero(monkeypatch):
    _patch_pipeline(monkeypatch, [])
    gs = se.DICSearchEngine().grounded_search(_QUERY)
    assert gs.result_count == 0
    assert gs.citation_quality == 0.0
    assert gs.results == []


def test_grounded_search_citations_mandatory(monkeypatch):
    _patch_pipeline(monkeypatch, [(_STRONG, 0.5), (_WEAK, 0.5)])
    gs = se.DICSearchEngine().grounded_search(_QUERY)
    assert gs.results  # non-empty
    for r in gs.results:
        assert r.citation.doc_id  # every returned result carries a citation


def test_grounded_search_to_dict_shape(monkeypatch):
    _patch_pipeline(monkeypatch, [(_STRONG, 0.5)])
    d = se.DICSearchEngine().grounded_search(_QUERY).to_dict()
    assert set(d) == {"query", "results", "result_count", "citation_quality", "origin", "anomaly_report"}
    assert d["results"][0]["attribution_score"] > 0.0


# --------------------------------------------------------------------------- #
# Per-answer citation-quality score — AI-assisted generation (answer)
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        return _Resp("The forecast projects growth [1].")


def _result(i, content, attribution_score):
    r = se.DICSearchResult(
        chunk_id=f"chunk-{i}",
        doc_id=f"doc-{i}",
        doc_title=f"Doc {i}",
        content=content,
        score=1.0,
        citation=se.Citation(doc_id=f"doc-{i}", doc_title=f"Doc {i}", chunk_id=f"chunk-{i}"),
    )
    r.attribution_score = attribution_score
    return r


def test_answer_returns_citation_quality(monkeypatch):
    monkeypatch.setattr(
        se.DICSearchEngine, "search",
        lambda self, *a, **k: [_result(1, _STRONG, 0.9), _result(2, _WEAK, 0.1)],
    )
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    ans = se.DICSearchEngine().answer(_QUERY)
    assert ans.grounded is True
    # Mean attribution over the two used results: (0.9 + 0.1) / 2 = 0.5.
    assert ans.citation_quality == pytest.approx(0.5)
    assert "citation_quality" in ans.to_dict()


def test_answer_no_evidence_quality_zero(monkeypatch):
    monkeypatch.setattr(se.DICSearchEngine, "search", lambda self, *a, **k: [])
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    ans = se.DICSearchEngine().answer(_QUERY)
    assert ans.grounded is False
    assert ans.refusal_reason == "no_evidence"
    assert ans.citation_quality == 0.0
