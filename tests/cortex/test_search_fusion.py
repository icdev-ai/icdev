# CUI // SP-CTI
"""Golden tests for cross-backend RRF fusion + dedupe + rerank (ctx-search-03).

fuse_results is exercised directly with hand-built CortexSearchResult lists
so every fused score and ordering is asserted exactly (k=60 golden values).
The rerank pass is monkeypatched at the module attribute the service
resolves at call time (shim-aware: importlib + setattr), matching the
convention in tests/cortex/test_search_adapters.py.
"""
from __future__ import annotations

import importlib

import pytest

from tools.cortex import search_service
from tools.cortex.schemas import Citation, CortexContext, CortexSearchResult

K = 60  # rrf_k default, pinned in args/cortex_config.yaml

_TABLES = {
    "rag": "compliance_documents",
    "graph": "kg_nodes",
    "dic": "dic_documents",
    "kb": "knowledge_patterns",
}


def _r(backend, source_id, content="", score=0.5, table="", raw=None):
    """Build a CortexSearchResult the way the backend adapters do."""
    return CortexSearchResult(
        content=content or f"{backend}-{source_id}",
        score=score,
        backend=backend,
        citation=Citation(
            source_id=source_id,
            source_type=backend,
            source_table=table or _TABLES.get(backend, "t"),
        ),
        raw_scores=dict(raw or {}),
    )


def _patch_rerank(monkeypatch, fake=None, forbid=False, raise_exc=None):
    """Replace tools.rag.reranker.rerank_results; returns the call log."""
    mod = importlib.import_module("tools.rag.reranker")
    calls = []

    def _fake(query, results, top_k=5, config=None):
        calls.append(
            {"query": query, "n": len(results), "top_k": top_k, "config": config}
        )
        if forbid:
            raise AssertionError("rerank pass must not run")
        if raise_exc:
            raise raise_exc
        if fake:
            return fake(results, top_k)
        return results

    monkeypatch.setattr(mod, "rerank_results", _fake)
    return calls


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_rrf_k_comes_from_cortex_config():
    cfg = search_service.load_cortex_config(refresh=True)
    fusion = (cfg.get("search") or {}).get("fusion") or {}
    assert fusion.get("rrf_k") == 60
    assert fusion.get("rerank_enabled") is True
    assert fusion.get("rerank_top_n") == 10


# ---------------------------------------------------------------------------
# Golden fused ordering (k=60, no dedupe involved)
# ---------------------------------------------------------------------------


def test_golden_two_backend_ordering(monkeypatch):
    calls = _patch_rerank(monkeypatch)  # passthrough spy
    rag = [_r("rag", "a1"), _r("rag", "a2"), _r("rag", "a3")]
    dic = [_r("dic", "b1"), _r("dic", "b2")]

    fused = search_service.fuse_results("q", [rag, dic])

    # Ties (same rank in different backends) keep backend order: rag first.
    assert [r.citation.source_id for r in fused] == ["a1", "b1", "a2", "b2", "a3"]
    assert [r.raw_scores["fused_rank"] for r in fused] == [1, 2, 3, 4, 5]
    assert [r.raw_scores["rrf"] for r in fused] == pytest.approx(
        [1 / (K + 1), 1 / (K + 1), 1 / (K + 2), 1 / (K + 2), 1 / (K + 3)]
    )
    # Scores normalized 0-1 against the fused peak.
    assert [r.score for r in fused] == pytest.approx(
        [1.0, 1.0, (K + 1) / (K + 2), (K + 1) / (K + 2), (K + 1) / (K + 3)]
    )
    # Multi-backend -> rerank pass triggered over the fused top-N.
    assert len(calls) == 1
    assert calls[0]["n"] == 5
    assert calls[0]["top_k"] == 5  # min(rerank_top_n=10, len=5)


def test_tie_order_follows_backend_list_order(monkeypatch):
    _patch_rerank(monkeypatch)
    rag = [_r("rag", "a1"), _r("rag", "a2")]
    dic = [_r("dic", "b1"), _r("dic", "b2")]

    fused_rag_first = search_service.fuse_results("q", [rag, dic])
    # Fresh objects — fuse_results mutates scores/raw_scores in place.
    rag2 = [_r("rag", "a1"), _r("rag", "a2")]
    dic2 = [_r("dic", "b1"), _r("dic", "b2")]
    fused_dic_first = search_service.fuse_results("q", [dic2, rag2])

    assert [r.citation.source_id for r in fused_rag_first] == ["a1", "b1", "a2", "b2"]
    assert [r.citation.source_id for r in fused_dic_first] == ["b1", "a1", "b2", "a2"]


def test_explicit_rrf_k_overrides_config(monkeypatch):
    _patch_rerank(monkeypatch)
    rag = [_r("rag", "a1"), _r("rag", "a2")]
    kb = [_r("kb", "p1")]

    fused = search_service.fuse_results("q", [rag, kb], rrf_k=0, rerank=False)

    assert [r.raw_scores["rrf"] for r in fused] == pytest.approx([1.0, 1.0, 0.5])
    assert [r.citation.source_id for r in fused] == ["a1", "p1", "a2"]


def test_fusion_is_deterministic(monkeypatch):
    _patch_rerank(monkeypatch)

    def run():
        lists = [
            [_r("rag", "a1"), _r("rag", "a2")],
            [_r("dic", "b1"), _r("dic", "b2")],
            [_r("kb", "p1")],
        ]
        return [
            (r.citation.source_id, r.score)
            for r in search_service.fuse_results("q", lists)
        ]

    assert run() == run()


# ---------------------------------------------------------------------------
# Dedupe across backends
# ---------------------------------------------------------------------------


def test_duplicates_collapse_with_merged_provenance(monkeypatch):
    _patch_rerank(monkeypatch)
    # doc-1 surfaces via rag (rank 1) AND dic (rank 2) — same provenance key.
    rag = [
        _r("rag", "doc-1", content="rag view of doc-1",
           table="dic_documents", raw={"vector": 0.8, "score": 0.9}),
        _r("rag", "a2"),
    ]
    dic = [
        _r("dic", "d-other"),
        _r("dic", "doc-1", content="dic view of doc-1",
           table="dic_documents", raw={"score": 0.7}),
    ]

    fused = search_service.fuse_results("q", [rag, dic])

    ids = [r.citation.source_id for r in fused]
    assert ids.count("doc-1") == 1
    assert len(fused) == 3
    top = fused[0]
    # Summed contributions (1/61 + 1/62) beat every single-backend hit.
    assert top.citation.source_id == "doc-1"
    assert top.raw_scores["rrf"] == pytest.approx(1 / (K + 1) + 1 / (K + 2))
    assert top.score == 1.0
    # Best-ranked duplicate (rag, rank 1) supplies content/citation.
    assert top.content == "rag view of doc-1"
    assert top.backend == "rag"
    # raw_scores merged; colliding key namespaced by the merged-in backend.
    assert top.raw_scores["vector"] == 0.8
    assert top.raw_scores["score"] == 0.9
    assert top.raw_scores["dic:score"] == 0.7
    # Merged provenance records which backends agreed.
    assert top.metadata["fused_backends"] == ["rag", "dic"]
    # Non-duplicates carry no fused_backends marker.
    assert "fused_backends" not in fused[1].metadata


def test_dedupe_keeps_best_ranked_representative(monkeypatch):
    _patch_rerank(monkeypatch)
    # doc-1 is rank 2 in rag but rank 1 in dic -> dic version wins.
    rag = [_r("rag", "a1"), _r("rag", "doc-1", content="rag copy", table="dic_documents")]
    dic = [_r("dic", "doc-1", content="dic copy", table="dic_documents")]

    fused = search_service.fuse_results("q", [rag, dic])

    top = fused[0]
    assert top.citation.source_id == "doc-1"
    assert top.content == "dic copy"
    assert top.metadata["fused_backends"] == ["rag", "dic"]


def test_results_without_source_id_never_merge(monkeypatch):
    _patch_rerank(monkeypatch)
    rag = [_r("rag", "", content="anon rag", table="")]
    kb = [_r("kb", "", content="anon kb", table="")]

    fused = search_service.fuse_results("q", [rag, kb])

    assert len(fused) == 2


# ---------------------------------------------------------------------------
# Single-backend pass-through
# ---------------------------------------------------------------------------


def test_single_backend_passes_through_untouched(monkeypatch):
    _patch_rerank(monkeypatch, forbid=True)
    rag = [_r("rag", "a1", score=0.91), _r("rag", "a2", score=0.4)]

    fused = search_service.fuse_results("q", [rag, [], []])

    assert fused == rag
    assert fused[0] is rag[0] and fused[1] is rag[1]
    assert fused[0].score == 0.91  # scores not rewritten
    assert "fused_rank" not in fused[0].raw_scores
    assert "rrf" not in fused[0].raw_scores


def test_no_backends_returns_empty(monkeypatch):
    _patch_rerank(monkeypatch, forbid=True)
    assert search_service.fuse_results("q", []) == []
    assert search_service.fuse_results("q", [[], []]) == []


# ---------------------------------------------------------------------------
# Final rerank pass
# ---------------------------------------------------------------------------


def test_rerank_reorders_and_preserves_fused_rank(monkeypatch):
    def fake(stubs, top_k):
        assert top_k == 3
        # Promote fused #3 above fused #1; drop #2 from the reranked block.
        stubs[2].final_score = 1.0
        stubs[2].rerank_score = 0.95
        stubs[0].final_score = 0.5
        stubs[0].rerank_score = 0.4
        return [stubs[2], stubs[0]]

    _patch_rerank(monkeypatch, fake=fake)
    rag = [_r("rag", "a1"), _r("rag", "a2"), _r("rag", "a3")]
    dic = [_r("dic", "b1"), _r("dic", "b2")]

    fused = search_service.fuse_results("q", [rag, dic], rerank_top_n=3)

    # Pre-rerank fused order was a1, b1, a2, b2, a3; reranked block first,
    # unreturned results follow in fused order.
    assert [r.citation.source_id for r in fused] == ["a2", "a1", "b1", "b2", "a3"]
    # Pre-rerank ordering preserved in raw_scores["fused_rank"].
    assert fused[0].raw_scores["fused_rank"] == 3
    assert fused[1].raw_scores["fused_rank"] == 1
    assert fused[0].raw_scores["fused_rerank"] == 0.95
    # Blended scores applied and final list normalized 0-1.
    assert fused[0].score == 1.0
    assert fused[1].score == pytest.approx(0.5)
    assert all(0.0 <= r.score <= 1.0 for r in fused)


def test_rerank_only_triggers_on_multi_backend(monkeypatch):
    calls = _patch_rerank(monkeypatch)
    search_service.fuse_results("q", [[_r("rag", "a1"), _r("rag", "a2")]])
    assert calls == []  # single backend: no rerank

    search_service.fuse_results(
        "q", [[_r("rag", "a1")], [_r("kb", "p1")]]
    )
    assert len(calls) == 1  # two backends: rerank pass ran


def test_rerank_can_be_disabled_via_flag(monkeypatch):
    calls = _patch_rerank(monkeypatch)
    search_service.fuse_results(
        "q", [[_r("rag", "a1")], [_r("kb", "p1")]], rerank=False
    )
    assert calls == []


def test_rerank_failure_keeps_fused_order(monkeypatch, caplog):
    _patch_rerank(monkeypatch, raise_exc=RuntimeError("reranker down"))
    rag = [_r("rag", "a1"), _r("rag", "a2")]
    dic = [_r("dic", "b1")]

    with caplog.at_level("WARNING"):
        fused = search_service.fuse_results("q", [rag, dic])

    assert [r.citation.source_id for r in fused] == ["a1", "b1", "a2"]
    assert [r.score for r in fused] == pytest.approx(
        [1.0, 1.0, (K + 1) / (K + 2)]
    )
    assert "rerank pass failed" in caplog.text


# ---------------------------------------------------------------------------
# search_all wiring
# ---------------------------------------------------------------------------


def test_search_all_fuses_and_dedupes_across_backends(monkeypatch):
    _patch_rerank(monkeypatch)
    monkeypatch.setitem(
        search_service.BACKEND_ADAPTERS,
        "rag",
        lambda q, top_k=5, ctx=None: [
            _r("rag", "doc-1", table="dic_documents"),
            _r("rag", "a2"),
        ],
    )
    monkeypatch.setitem(
        search_service.BACKEND_ADAPTERS,
        "dic",
        lambda q, top_k=5, ctx=None: [_r("dic", "doc-1", table="dic_documents")],
    )

    results = search_service.search_all(
        "q", ctx=CortexContext(tenant_id="t"), backends=["rag", "dic"]
    )

    assert [r.citation.source_id for r in results] == ["doc-1", "a2"]
    assert results[0].metadata["fused_backends"] == ["rag", "dic"]
    assert results[0].score == 1.0
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_all_single_backend_untouched(monkeypatch):
    _patch_rerank(monkeypatch, forbid=True)
    native = [_r("kb", "p1", score=0.85)]
    monkeypatch.setitem(
        search_service.BACKEND_ADAPTERS, "kb", lambda q, top_k=5, ctx=None: native
    )
    monkeypatch.setitem(
        search_service.BACKEND_ADAPTERS, "rag", lambda q, top_k=5, ctx=None: []
    )

    results = search_service.search_all("q", backends=["kb", "rag"])

    assert results == native
    assert results[0].score == 0.85
    assert "rrf" not in results[0].raw_scores


def test_fusion_importable_via_canonical_namespace():
    from icdev.tools.cortex.search_service import fuse_results  # noqa: F401
