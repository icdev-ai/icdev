# CUI // SP-CTI
"""Tests for the Cortex search backend adapters (ctx-search-01).

Each backend is monkeypatched at the module attribute the adapter resolves
at call time (shim-aware: importlib + setattr), returning fixture shapes
built from the real native result classes so field names stay honest.
"""
from __future__ import annotations

import importlib

from tools.cortex import search_service
from tools.cortex.schemas import CortexContext, CortexSearchResult


# ---------------------------------------------------------------------------
# Fixture shapes captured from the real backend modules
# ---------------------------------------------------------------------------


def _rag_fixture_results():
    from tools.rag.vector_store_provider import SearchResult

    return [
        SearchResult(
            chunk_id="ch-1",
            content="FedRAMP AC-2 account management guidance.",
            source_type="compliance_docs",
            source_id="row-42",
            source_table="compliance_documents",
            chunk_index=0,
            score=0.83,
            bm25_score=7.1,
            time_decay_score=0.80,
            rerank_score=0.91,
            final_score=0.91,
            tier="hot",
            classification="CUI",
        ),
        # final_score past 1.0 exercises clamping without losing the raw value
        SearchResult(
            chunk_id="ch-2",
            content="Account disablement procedures.",
            source_type="compliance_docs",
            source_id="row-43",
            source_table="compliance_documents",
            score=0.61,
            final_score=1.7,
        ),
    ]


# Shape of tools/knowledge_graph/graph_rag.py::retrieve() "ok" return.
_GRAPH_FIXTURE = {
    "status": "ok",
    "query": "firewall",
    "profile": "network",
    "profile_auto_detected": True,
    "nodes_matched": 2,
    "nodes_returned": 2,
    "edges_returned": 1,
    "neighbor_count": 1,
    "rrf_fusion": True,
    "ontology_expansion": False,
    "query_entity_types": ["network_firewall"],
    "nodes": [
        {
            "id": "node-1",
            "graph_id": "g-1",
            "label": "NYC-FWLL-01",
            "entity_type": "network_firewall",
            "properties": '{"vendor": "cisco"}',
            "centrality": 0.42,
            "created_at": "2026-01-01T00:00:00",
            "score": 1.6,  # additive profile score past 1.0 — needs peak-norm
            "hybrid_rank": 0.032,
            "is_neighbor": False,
        },
        {
            "id": "node-2",
            "graph_id": "g-1",
            "label": "CORE-RTR-07",
            "entity_type": "network_router",
            "properties": "",
            "centrality": 0.21,
            "created_at": "2026-01-01T00:00:00",
            "score": 0.8,
            "hybrid_rank": 0.016,
            "is_neighbor": True,
        },
    ],
    "edges": [
        {
            "id": "e-1",
            "graph_id": "g-1",
            "source_id": "node-1",
            "target_id": "node-2",
            "relationship": "connects_to",
            "weight": 1.0,
            "properties": "{}",
            "created_at": "2026-01-01T00:00:00",
        }
    ],
    "compressed": False,
    "semantic_search": True,
    "context": "NYC-FWLL-01 connects_to CORE-RTR-07",
    "retrieval_ms": 12,
}


def _dic_fixture_results():
    from tools.document_intelligence.search_engine import Citation, DICSearchResult

    return [
        DICSearchResult(
            chunk_id="dic-ch-9",
            doc_id="doc-77",
            doc_title="Network Security SOP",
            collection_id="col-3",
            content="Firewall rules shall be reviewed quarterly.",
            page=4,
            section="3.2",
            score=0.72,
            matched_terms=["firewall"],
            kg_path=["doc-77", "node-1"],
            citation=Citation(
                doc_id="doc-77",
                doc_title="Network Security SOP",
                page=4,
                section="3.2",
                chunk_id="dic-ch-9",
                classification="CUI",
            ),
            sha256="abc123",
            attribution_pct=88,
            attribution_score=0.9,
        ),
    ]


# Shape of tools/mcp/knowledge_server.py::handle_search_knowledge() return.
_KB_FIXTURE = {
    "query": "timeout",
    "results": [
        {
            "id": 7,
            "name": "db-connection-timeout",
            "pattern_type": "error",
            "description": "PostgreSQL connection pool exhaustion under load.",
            "detection_rule": "OperationalError.*timeout",
            "solution": "Increase pool size and add idle-in-transaction timeout.",
            "confidence": 0.85,
            "use_count": 12,
        }
    ],
    "count": 1,
}


# ---------------------------------------------------------------------------
# Monkeypatch helpers — patch the module object the adapter imports at call
# time (importlib + setattr, per the shim-aware patching convention)
# ---------------------------------------------------------------------------


def _patch_rag(monkeypatch, results=None, captured=None, raise_exc=None):
    mod = importlib.import_module("tools.rag.retriever")

    class FakeRetriever:
        def __init__(self, tenant_id=""):
            if captured is not None:
                captured["tenant_id"] = tenant_id

        def search(self, query, top_k=5, **kwargs):
            if raise_exc:
                raise raise_exc
            if captured is not None:
                captured["query"] = query
                captured["top_k"] = top_k
            return results if results is not None else []

    monkeypatch.setattr(mod, "RAGRetriever", FakeRetriever)


def _patch_graph(monkeypatch, result=None, raise_exc=None):
    mod = importlib.import_module("tools.knowledge_graph.graph_rag")

    def fake_retrieve(query, top_k=10, compress=True, **kwargs):
        if raise_exc:
            raise raise_exc
        return result

    monkeypatch.setattr(mod, "retrieve", fake_retrieve)


def _patch_dic(monkeypatch, results=None, captured=None, raise_exc=None):
    mod = importlib.import_module("tools.document_intelligence.search_engine")

    class FakeEngine:
        def __init__(self, tenant_id="default"):
            if captured is not None:
                captured["tenant_id"] = tenant_id

        def search(self, query, top_k=10, clearance=None, **kwargs):
            if raise_exc:
                raise raise_exc
            if captured is not None:
                captured["query"] = query
                captured["top_k"] = top_k
                captured["clearance"] = clearance
            return results if results is not None else []

    monkeypatch.setattr(mod, "DICSearchEngine", FakeEngine)


def _patch_kb(monkeypatch, result=None, raise_exc=None):
    mod = importlib.import_module("tools.mcp.knowledge_server")

    def fake_handler(args):
        if raise_exc:
            raise raise_exc
        return result

    monkeypatch.setattr(mod, "handle_search_knowledge", fake_handler)


# ---------------------------------------------------------------------------
# RAG adapter
# ---------------------------------------------------------------------------


def test_rag_adapter_normalizes_results(monkeypatch):
    captured = {}
    _patch_rag(monkeypatch, results=_rag_fixture_results(), captured=captured)
    ctx = CortexContext(tenant_id="tenant-a", classification="CUI")

    results = search_service.search_rag("FedRAMP AC-2", top_k=5, ctx=ctx)

    assert len(results) == 2
    assert all(isinstance(r, CortexSearchResult) for r in results)
    assert all(r.backend == "rag" for r in results)
    assert all(0.0 <= r.score <= 1.0 for r in results)
    first = results[0]
    assert first.score == 0.91
    assert first.content == "FedRAMP AC-2 account management guidance."
    assert first.citation.source_id == "row-42"
    assert first.citation.source_table == "compliance_documents"
    assert first.citation.source_type == "rag_chunk"
    assert first.citation.classification == "CUI"
    assert first.citation.snippet
    # Native scores preserved verbatim
    assert first.raw_scores == {
        "vector": 0.83,
        "bm25": 7.1,
        "time_decay": 0.80,
        "rerank": 0.91,
        "final": 0.91,
    }
    # Out-of-range final_score is clamped, raw value kept
    assert results[1].score == 1.0
    assert results[1].raw_scores["final"] == 1.7


def test_rag_adapter_passes_tenant_and_topk(monkeypatch):
    captured = {}
    _patch_rag(monkeypatch, results=[], captured=captured)

    search_service.search_rag("q", top_k=3, ctx=CortexContext(tenant_id="tenant-b"))

    assert captured["tenant_id"] == "tenant-b"
    assert captured["top_k"] == 3


def test_rag_adapter_exception_isolated(monkeypatch, caplog):
    _patch_rag(monkeypatch, raise_exc=RuntimeError("vector store down"))

    with caplog.at_level("WARNING"):
        assert search_service.search_rag("q") == []
    assert "rag backend failed" in caplog.text


# ---------------------------------------------------------------------------
# Graph adapter
# ---------------------------------------------------------------------------


def test_graph_adapter_normalizes_nodes(monkeypatch):
    _patch_graph(monkeypatch, result=_GRAPH_FIXTURE)

    results = search_service.search_graph("firewall", top_k=10)

    assert len(results) == 2
    assert all(r.backend == "graph" for r in results)
    assert all(0.0 <= r.score <= 1.0 for r in results)
    first = results[0]
    # Peak-normalized: 1.6/1.6 -> 1.0, 0.8/1.6 -> 0.5 (order preserved)
    assert first.score == 1.0
    assert results[1].score == 0.5
    assert first.citation.source_id == "node-1"
    assert first.citation.source_type == "kg_node"
    assert first.citation.source_table == "kg_nodes"
    assert first.citation.title == "NYC-FWLL-01"
    assert "NYC-FWLL-01" in first.content
    assert "network_firewall" in first.content
    assert first.strategy == "network"
    assert first.raw_scores["score"] == 1.6
    assert first.raw_scores["centrality"] == 0.42
    assert results[1].metadata["is_neighbor"] is True


def test_graph_adapter_error_status_returns_empty(monkeypatch):
    _patch_graph(
        monkeypatch,
        result={"status": "error", "nodes": [], "context": "Retrieval error: boom"},
    )
    assert search_service.search_graph("q") == []


def test_graph_adapter_exception_isolated(monkeypatch, caplog):
    _patch_graph(monkeypatch, raise_exc=RuntimeError("sqlite locked"))

    with caplog.at_level("WARNING"):
        assert search_service.search_graph("q") == []
    assert "graph backend failed" in caplog.text


# ---------------------------------------------------------------------------
# DIC adapter
# ---------------------------------------------------------------------------


def test_dic_adapter_normalizes_results(monkeypatch):
    captured = {}
    _patch_dic(monkeypatch, results=_dic_fixture_results(), captured=captured)
    ctx = CortexContext(tenant_id="tenant-c", classification="CUI")

    results = search_service.search_dic("firewall review", top_k=10, ctx=ctx)

    assert len(results) == 1
    r = results[0]
    assert r.backend == "dic"
    assert r.strategy == "grounded"
    assert 0.0 <= r.score <= 1.0
    assert r.score == 0.72
    assert r.citation.source_id == "doc-77"
    assert r.citation.source_type == "dic_document"
    assert r.citation.title == "Network Security SOP"
    assert r.citation.url == "/document-intelligence/doc/doc-77"
    assert r.citation.classification == "CUI"
    # clearance-aware ranking output preserved
    assert r.citation.clearance_required == "CUI"
    assert r.raw_scores["attribution_score"] == 0.9
    assert r.raw_scores["attribution_pct"] == 88
    assert r.metadata["page"] == 4
    assert r.metadata["section"] == "3.2"
    assert r.metadata["matched_terms"] == ["firewall"]


def test_dic_adapter_passes_tenant_and_clearance(monkeypatch):
    captured = {}
    _patch_dic(monkeypatch, results=[], captured=captured)
    ctx = CortexContext(tenant_id="tenant-d", classification="SECRET")

    search_service.search_dic("q", top_k=4, ctx=ctx)

    assert captured["tenant_id"] == "tenant-d"
    assert captured["clearance"] == "SECRET"
    assert captured["top_k"] == 4


def test_dic_adapter_exception_isolated(monkeypatch, caplog):
    _patch_dic(monkeypatch, raise_exc=RuntimeError("db down"))

    with caplog.at_level("WARNING"):
        assert search_service.search_dic("q") == []
    assert "dic backend failed" in caplog.text


# ---------------------------------------------------------------------------
# KB adapter
# ---------------------------------------------------------------------------


def test_kb_adapter_normalizes_patterns(monkeypatch):
    _patch_kb(monkeypatch, result=_KB_FIXTURE)

    results = search_service.search_kb("timeout", top_k=10)

    assert len(results) == 1
    r = results[0]
    assert r.backend == "kb"
    assert r.strategy == "keyword"
    assert r.score == 0.85
    assert r.citation.source_id == "7"
    assert r.citation.source_type == "kb_entry"
    assert r.citation.source_table == "knowledge_patterns"
    assert r.citation.title == "db-connection-timeout"
    assert "pool exhaustion" in r.content
    assert "Solution:" in r.content
    assert r.raw_scores == {"confidence": 0.85, "use_count": 12}
    assert r.metadata["pattern_type"] == "error"


def test_kb_adapter_exception_isolated(monkeypatch, caplog):
    _patch_kb(monkeypatch, raise_exc=ValueError("'query' is required"))

    with caplog.at_level("WARNING"):
        assert search_service.search_kb("") == []
    assert "kb backend failed" in caplog.text


# ---------------------------------------------------------------------------
# search_all fan-out
# ---------------------------------------------------------------------------


def test_search_all_merges_and_sorts(monkeypatch):
    _patch_rag(monkeypatch, results=_rag_fixture_results())
    _patch_graph(monkeypatch, result=_GRAPH_FIXTURE)
    _patch_dic(monkeypatch, results=_dic_fixture_results())
    _patch_kb(monkeypatch, result=_KB_FIXTURE)

    results = search_service.search_all("q", top_k=5)

    assert {r.backend for r in results} == {"rag", "graph", "dic", "kb"}
    # RRF fusion: results are ordered by the fused rrf score (not raw per-backend
    # score, which isn't cross-backend comparable). Every result carries an rrf.
    rrf = [(r.raw_scores or {}).get("rrf", 0.0) for r in results]
    assert rrf == sorted(rrf, reverse=True)
    assert all((r.raw_scores or {}).get("rrf") for r in results)


def test_search_all_survives_one_backend_failure(monkeypatch):
    _patch_rag(monkeypatch, raise_exc=RuntimeError("down"))
    _patch_graph(monkeypatch, result=_GRAPH_FIXTURE)
    _patch_dic(monkeypatch, results=_dic_fixture_results())
    _patch_kb(monkeypatch, result=_KB_FIXTURE)

    results = search_service.search_all("q")

    assert {r.backend for r in results} == {"graph", "dic", "kb"}


def test_search_all_skips_unknown_backend(monkeypatch):
    _patch_kb(monkeypatch, result=_KB_FIXTURE)

    results = search_service.search_all("q", backends=["kb", "nope"])

    assert [r.backend for r in results] == ["kb"]


def test_adapters_importable_via_canonical_namespace():
    from icdev.tools.cortex.search_service import (  # noqa: F401
        BACKEND_ADAPTERS,
        search_all,
        search_dic,
        search_graph,
        search_kb,
        search_rag,
        search_sme,
    )

    assert set(BACKEND_ADAPTERS) == {"rag", "graph", "dic", "kb", "sme"}


# ---------------------------------------------------------------------------
# Shared fan-out pool (no per-call executor leak)
# ---------------------------------------------------------------------------
def test_search_executor_is_shared_and_not_shutdown_per_call(monkeypatch):
    ex1 = search_service._get_search_executor()
    ex2 = search_service._get_search_executor()
    assert ex1 is ex2, "the search pool must be a single process-wide instance"

    def _fast(query, top_k=5, ctx=None):
        return [CortexSearchResult(backend="rag", content="x", score=0.9)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", _fast)
    _, ran, timed_out = search_service._run_backends(
        "q", 5, None, ["rag"], {"timeouts": {"default": 5.0}}
    )
    assert ran == ["rag"]
    assert not timed_out
    # A single call must NOT tear the shared pool down (the old per-call
    # executor.shutdown() is gone).
    assert search_service._get_search_executor() is ex1
    assert ex1._shutdown is False


def test_backend_timeout_returns_partial_without_tearing_down_pool(monkeypatch):
    import time as _time

    ex = search_service._get_search_executor()

    def _fast(query, top_k=5, ctx=None):
        return [CortexSearchResult(backend="rag", content="fast", score=0.9)]

    def _slow(query, top_k=5, ctx=None):
        _time.sleep(2.0)  # exceeds the graph timeout below
        return [CortexSearchResult(backend="graph", content="slow", score=0.8)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", _fast)
    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "graph", _slow)

    results, ran, timed_out = search_service._run_backends(
        "q", 5, None, ["rag", "graph"],
        {"timeouts": {"default": 10.0, "graph": 0.2}},
    )
    # Partial results: the fast backend is returned, the slow one is abandoned.
    assert "graph" in timed_out
    assert {r.backend for r in results} == {"rag"}
    # The shared pool is untouched — same instance, still alive for reuse.
    assert search_service._get_search_executor() is ex
    assert ex._shutdown is False


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------
def _r(backend, score, sid):
    from tools.cortex.schemas import Citation
    return CortexSearchResult(content=f"{sid}", score=score, backend=backend,
                              citation=Citation(source_id=sid))


def test_rrf_rewards_multi_backend_agreement():
    # doc "shared" is found by rag (rank 2) AND graph (rank 1); a rag-only "solo"
    # is rank 1 in rag with a higher raw score. RRF should still lift "shared"
    # because two backends agree on it.
    rows = [
        _r("rag", 0.95, "solo"),
        _r("rag", 0.40, "shared"),
        _r("graph", 0.90, "shared"),
    ]
    fused = search_service._rrf_fuse(rows, k=60)
    ids = [r.citation.source_id for r in fused]
    assert ids[0] == "shared"  # agreement beats a single high raw score
    # 'shared' fused once (dedup across backends), so 2 results total.
    assert len(fused) == 2
    top = fused[0]
    # rrf = 1/(60+2) [rag rank2] + 1/(60+1) [graph rank1] (rounded to 8 places)
    assert abs(top.raw_scores["rrf"] - (1/62 + 1/61)) < 1e-6


def test_rrf_preserves_raw_score_and_is_deterministic():
    rows = [_r("rag", 0.3, "a"), _r("rag", 0.9, "b"), _r("kb", 0.5, "c")]
    fused = search_service._rrf_fuse(rows, k=60)
    # Raw .score untouched (CRAG/callers rely on it).
    assert {r.citation.source_id: r.score for r in fused} == {"a": 0.3, "b": 0.9, "c": 0.5}
    # Deterministic across runs.
    again = search_service._rrf_fuse(list(rows), k=60)
    assert [r.citation.source_id for r in fused] == [r.citation.source_id for r in again]


def test_rrf_empty_is_empty():
    assert search_service._rrf_fuse([]) == []
