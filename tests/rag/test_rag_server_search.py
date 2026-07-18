# CUI // SP-CTI
"""Dedicated tests for the ``rag_search`` MCP handler (hcx-ctx-06).

Before hcx-ctx-06 ``handle_rag_search`` forwarded ``filters=`` and ``agent_id=``
kwargs that ``RAGRetriever.search()`` does not accept, so *every* MCP
``rag_search`` call raised ``TypeError`` internally and returned an error with
an empty ``results`` list — a silent dead-evidence-gatherer (no dedicated test
had ever exercised the path). These tests lock in the fix:

- happy path returns REAL results from a seeded corpus by driving the *actual*
  ``RAGRetriever.search()`` (only the embedding provider and vector store are
  faked, so the real two-stage pipeline + param signature run end-to-end);
- every param the MCP schema advertises is accepted without ``TypeError``
  (kwarg regression);
- tenant scoping reaches the vector-store filters;
- ``source_type`` maps onto the retriever's ``source_types`` list;
- the error path returns a structured error only for genuine failures;
- the MCP tool schema no longer advertises a param the handler drops.
"""
from __future__ import annotations

import importlib

from tools.mcp import rag_server
from tools.rag.vector_store_provider import SearchResult


# ---------------------------------------------------------------------------
# Fakes: a seeded corpus + an embedding provider, injected so the REAL
# RAGRetriever.search() runs (its param signature is what regressed).
# ---------------------------------------------------------------------------


def _seed_corpus():
    """A small seeded corpus as the vector store would return it."""
    return [
        SearchResult(
            chunk_id="ch-1",
            content="FedRAMP AC-2 account management: accounts shall be reviewed.",
            source_type="compliance_docs",
            source_id="row-1",
            source_table="compliance_documents",
            chunk_index=0,
            score=0.88,
            tier="hot",
            classification="CUI",
        ),
        SearchResult(
            chunk_id="ch-2",
            content="Account disablement procedures for inactive users.",
            source_type="compliance_docs",
            source_id="row-2",
            source_table="compliance_documents",
            chunk_index=0,
            score=0.71,
            tier="hot",
            classification="CUI",
        ),
    ]


class _FakeStore:
    """Vector store stand-in; records the filters each query is scoped by."""

    def __init__(self, corpus):
        self._corpus = corpus
        self.filter_calls = []

    def search(self, query_embedding, top_k=50, filters=None):
        self.filter_calls.append(dict(filters or {}))
        # Return fresh copies so per-call score mutation can't leak between tests.
        return [
            SearchResult(**{**r.to_dict(), "metadata": {}}) for r in self._corpus
        ]


class _FakeProvider:
    def embed(self, text):
        return [0.1] * 768


def _drive_real_retriever(monkeypatch, corpus=None, store_holder=None):
    """Patch the retriever's embedding provider + vector store factory so the
    genuine RAGRetriever.search() executes against a seeded corpus."""
    retr = importlib.import_module("tools.rag.retriever")
    store = _FakeStore(corpus if corpus is not None else _seed_corpus())
    if store_holder is not None:
        store_holder["store"] = store

    monkeypatch.setattr(retr, "_get_embedding_provider", lambda: _FakeProvider())

    class _FakeFactory:
        @staticmethod
        def create(backend="", tenant_id=None, config=None):
            return store

    monkeypatch.setattr(retr, "VectorStoreFactory", _FakeFactory)
    return store


# ---------------------------------------------------------------------------
# Happy path — REAL results end-to-end
# ---------------------------------------------------------------------------


def test_rag_search_happy_path_returns_real_results(monkeypatch):
    _drive_real_retriever(monkeypatch)

    out = rag_server.handle_rag_search(
        {"query": "FedRAMP AC-2 account management", "top_k": 5}
    )

    # No error, and REAL content flows back from the seeded corpus.
    assert "error" not in out
    assert out["results_count"] == 2
    assert len(out["results"]) == 2
    contents = {r["content"] for r in out["results"]}
    assert "FedRAMP AC-2 account management: accounts shall be reviewed." in contents
    # Result dicts are the real SearchResult.to_dict() shape.
    top = out["results"][0]
    assert top["chunk_id"] in {"ch-1", "ch-2"}
    assert "final_score" in top and "source_type" in top
    assert out["classification"] == "CUI // SP-CTI"
    assert out["query"] == "FedRAMP AC-2 account management"


def test_rag_search_accepts_every_advertised_param(monkeypatch):
    """Kwarg regression: every param the MCP schema advertises must be accepted
    end-to-end without the historical TypeError."""
    from tools.mcp.tool_registry import TOOL_REGISTRY

    advertised = set(
        TOOL_REGISTRY["rag_search"]["input_schema"]["properties"].keys()
    )
    # The schema must not advertise a param the handler silently drops.
    assert advertised == {"query", "top_k", "source_type", "tenant_id"}

    _drive_real_retriever(monkeypatch)
    out = rag_server.handle_rag_search(
        {
            "query": "FedRAMP AC-2",
            "top_k": 3,
            "source_type": "compliance_docs",
            "tenant_id": "tenant-a",
        }
    )
    assert "error" not in out, out
    assert out["results_count"] >= 1


# ---------------------------------------------------------------------------
# Param mapping — tenant scoping + source_type -> source_types
# ---------------------------------------------------------------------------


def test_rag_search_scopes_by_tenant(monkeypatch):
    holder = {}
    _drive_real_retriever(monkeypatch, store_holder=holder)

    out = rag_server.handle_rag_search(
        {"query": "AC-2", "tenant_id": "tenant-xyz"}
    )

    assert "error" not in out
    # tenant_id reached the vector-store filters on every scoped query.
    assert holder["store"].filter_calls
    assert all(f.get("tenant_id") == "tenant-xyz" for f in holder["store"].filter_calls)


def test_rag_search_maps_source_type_to_source_types(monkeypatch):
    holder = {}
    _drive_real_retriever(monkeypatch, store_holder=holder)

    out = rag_server.handle_rag_search(
        {"query": "AC-2", "source_type": "compliance_docs", "tenant_id": "t1"}
    )

    assert "error" not in out
    # The single source_type became a per-source-type scoped vector query.
    assert holder["store"].filter_calls
    assert all(
        f.get("source_type") == "compliance_docs"
        for f in holder["store"].filter_calls
    )


# ---------------------------------------------------------------------------
# Error paths — structured error only for GENUINE failures
# ---------------------------------------------------------------------------


def test_rag_search_requires_query():
    out = rag_server.handle_rag_search({"query": ""})
    assert out["error"] == "query is required"
    assert out["results"] == []


def test_rag_search_genuine_failure_returns_structured_error(monkeypatch):
    retr = importlib.import_module("tools.rag.retriever")

    class _Boom:
        def __init__(self, tenant_id=""):
            pass

        def search(self, query, **kwargs):
            raise RuntimeError("vector store unreachable")

    monkeypatch.setattr(retr, "RAGRetriever", _Boom)

    out = rag_server.handle_rag_search({"query": "AC-2"})
    assert out["results"] == []
    assert "vector store unreachable" in out["error"]
    # Crucially NOT the historical kwarg TypeError.
    assert "unexpected keyword argument" not in out["error"]


def test_rag_search_no_longer_raises_kwarg_typeerror(monkeypatch):
    """Regression guard: the real search() signature is honored, so the handler
    never produces the old 'unexpected keyword argument filters' error."""
    _drive_real_retriever(monkeypatch)
    out = rag_server.handle_rag_search(
        {"query": "AC-2", "source_type": "compliance_docs", "tenant_id": "t"}
    )
    assert "unexpected keyword argument" not in str(out.get("error", ""))
    assert out["results_count"] == 2
