"""Tests for the DIC community clusterizer + pre-compute summaries.

dic-adapt-07-d2: Lightweight deterministic clustering of chunks into
communities, with extractive summarization that degrades gracefully when the
LLM is unavailable. Results are stored in dic_community_summaries.
"""
from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")


@pytest.fixture
def dic_conn(icdev_db):
    """SQLite connection to the temp ICDEV DB with DIC tables."""
    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _insert_chunks(conn, chunks, links=None):
    """Helper: seed rag_chunks + dic_chunk_links."""
    for c in chunks:
        conn.execute(
            "INSERT INTO rag_chunks (id, content, tenant_id, classification) VALUES (?, ?, ?, ?)",
            (c["id"], c.get("content", ""), c.get("tenant_id", "default"), c.get("classification", "CUI")),
        )
    if links:
        for l in links:
            conn.execute(
                "INSERT INTO dic_chunk_links (link_id, doc_id, rag_chunk_id, collection_id, tenant_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    l.get("link_id", f"lnk-{l['rag_chunk_id']}"),
                    l.get("doc_id", ""),
                    l["rag_chunk_id"],
                    l.get("collection_id", "default"),
                    l.get("tenant_id", "default"),
                ),
            )
    conn.commit()


# --------------------------------------------------------------------------- #
# Extractive summary (pure, no LLM)
# --------------------------------------------------------------------------- #

def test_extractive_summary_basic():
    texts = [
        "The system architecture uses microservices. Each service is independently deployable. "
        "This improves resilience and scalability. The API gateway routes traffic.",
        "Microservices allow teams to deploy independently. Resilience is a key benefit. "
        "Scalability comes from horizontal scaling.",
    ]
    summary = se._extractive_summary(texts, max_sentences=2)
    assert summary
    assert len(summary) <= se._SUMMARY_MAX_CHARS
    # Should pull sentences containing the high-frequency significant terms.
    assert "microservice" in summary.lower() or "service" in summary.lower()


def test_extractive_summary_empty():
    assert se._extractive_summary([]) == ""
    assert se._extractive_summary([""]) == ""


def test_extractive_summary_deduplicates():
    texts = [
        "The quick brown fox jumps over the lazy dog.",
        "The quick brown fox jumps over the lazy dog.",
    ]
    summary = se._extractive_summary(texts, max_sentences=3)
    # Deduplication should prevent the exact same sentence appearing twice.
    assert summary.count("quick brown fox") == 1


# --------------------------------------------------------------------------- #
# Clustering logic
# --------------------------------------------------------------------------- #

def test_cluster_chunks_by_doc_id():
    chunks = [
        {"chunk_id": "c1", "content": "alpha beta gamma", "doc_id": "doc-a"},
        {"chunk_id": "c2", "content": "delta epsilon", "doc_id": "doc-a"},
        {"chunk_id": "c3", "content": "zeta eta theta", "doc_id": "doc-b"},
    ]
    clusters = se._cluster_chunks(chunks)
    assert len(clusters) == 2
    by_label = {c["label"]: c for c in clusters}
    assert "doc:doc-a" in by_label
    assert "doc:doc-b" in by_label
    assert set(by_label["doc:doc-a"]["chunk_ids"]) == {"c1", "c2"}
    assert set(by_label["doc:doc-b"]["chunk_ids"]) == {"c3"}


def test_cluster_orphan_chunks_by_terms():
    chunks = [
        {"chunk_id": "c1", "content": "machine learning models require training data", "doc_id": ""},
        {"chunk_id": "c2", "content": "training data improves machine learning models", "doc_id": ""},
        {"chunk_id": "c3", "content": "unrelated topic about baking bread", "doc_id": ""},
    ]
    clusters = se._cluster_chunks(chunks)
    # c1 and c2 should cluster together via term overlap; c3 is singleton.
    # The c1/c2 cluster label derives from the most common term in that cluster.
    multi = [c for c in clusters if c["entity_count"] > 1]
    assert len(multi) == 1
    assert multi[0]["chunk_ids"] == ["c1", "c2"]
    assert multi[0]["label"].startswith("term:")
    singletons = [c for c in clusters if c["entity_count"] == 1]
    assert len(singletons) >= 1


def test_cluster_empty():
    assert se._cluster_chunks([]) == []


# --------------------------------------------------------------------------- #
# KG community extraction (graceful absence)
# --------------------------------------------------------------------------- #

def test_kg_community_clusters_absent_tables(dic_conn):
    """When kg_nodes does not exist, _kg_community_clusters returns None."""
    result = se._kg_community_clusters(dic_conn, ["c1"], "default")
    assert result is None


# --------------------------------------------------------------------------- #
# LLM summary degradation
# --------------------------------------------------------------------------- #

def test_llm_community_summary_degrades_on_exception(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("model down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    assert se._llm_community_summary(["some text"]) is None


def test_llm_community_summary_none_response(monkeypatch):
    class _NoneRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            return None

    monkeypatch.setattr(router_mod, "LLMRouter", _NoneRouter)
    assert se._llm_community_summary(["some text"]) is None


# --------------------------------------------------------------------------- #
# End-to-end compute + persist + load
# --------------------------------------------------------------------------- #

def test_compute_community_summaries_persists(dic_conn):
    _insert_chunks(
        dic_conn,
        [
            {"id": "c1", "content": "The budget grew twelve percent in Q3. Hiring drove the increase."},
            {"id": "c2", "content": "Q3 revenue exceeded targets. Budget growth supported new staff."},
            {"id": "c3", "content": "Engineering shipped three major features. Release cadence improved."},
        ],
        [
            {"rag_chunk_id": "c1", "doc_id": "doc-budget", "collection_id": "col-1"},
            {"rag_chunk_id": "c2", "doc_id": "doc-budget", "collection_id": "col-1"},
            {"rag_chunk_id": "c3", "doc_id": "doc-eng", "collection_id": "col-1"},
        ],
    )
    results = se.compute_community_summaries(
        collection_id="col-1", tenant_id="default", conn=dic_conn
    )
    assert len(results) >= 2  # doc-budget + doc-eng
    # Verify DB state
    rows = dic_conn.execute(
        "SELECT community_id, label, entity_count, summary_text, summary_method "
        "FROM dic_community_summaries WHERE collection_id = ?",
        ("col-1",),
    ).fetchall()
    assert len(rows) == len(results)
    budgets = [r for r in rows if "budget" in r["label"]]
    assert len(budgets) >= 1 or any("doc:doc-budget" in r["label"] for r in rows)


def test_compute_community_summaries_collection_filter(dic_conn):
    _insert_chunks(
        dic_conn,
        [
            {"id": "c1", "content": "content for collection A"},
            {"id": "c2", "content": "content for collection B"},
        ],
        [
            {"rag_chunk_id": "c1", "doc_id": "doc-a", "collection_id": "col-a"},
            {"rag_chunk_id": "c2", "doc_id": "doc-b", "collection_id": "col-b"},
        ],
    )
    res_a = se.compute_community_summaries(
        collection_id="col-a", tenant_id="default", conn=dic_conn
    )
    assert len(res_a) == 1
    assert json.loads(res_a[0]["doc_ids"]) == ["doc-a"]

    res_b = se.compute_community_summaries(
        collection_id="col-b", tenant_id="default", conn=dic_conn
    )
    assert len(res_b) == 1
    assert json.loads(res_b[0]["doc_ids"]) == ["doc-b"]


def test_compute_community_summaries_llm_fallback_to_extractive(dic_conn, monkeypatch):
    """When LLM is requested but unavailable, summary falls back to extractive."""
    _insert_chunks(
        dic_conn,
        [{"id": "c1", "content": "The quick brown fox jumps over the lazy dog."}],
        [{"rag_chunk_id": "c1", "doc_id": "doc-1", "collection_id": "col-1"}],
    )

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    results = se.compute_community_summaries(
        collection_id="col-1", tenant_id="default", use_llm=True, conn=dic_conn
    )
    assert len(results) == 1
    assert results[0]["summary_method"] == "extractive"
    assert results[0]["summary_text"]  # non-empty


def test_load_community_summaries(dic_conn):
    _insert_chunks(
        dic_conn,
        [
            {"id": "c1", "content": "aaa bbb ccc"},
            {"id": "c2", "content": "ddd eee fff"},
        ],
        [
            {"rag_chunk_id": "c1", "doc_id": "doc-1", "collection_id": "col-x"},
            {"rag_chunk_id": "c2", "doc_id": "doc-2", "collection_id": "col-x"},
        ],
    )
    se.compute_community_summaries(
        collection_id="col-x", tenant_id="default", conn=dic_conn
    )
    loaded = se.load_community_summaries(
        collection_id="col-x", tenant_id="default", conn=dic_conn
    )
    assert len(loaded) >= 2
    for item in loaded:
        assert "community_id" in item
        assert isinstance(item["chunk_ids"], list)
        assert isinstance(item["doc_ids"], list)
        assert item["summary_text"]


def test_compute_community_summaries_no_chunks_empty(dic_conn):
    """When there are no chunks, compute returns empty list and writes nothing."""
    results = se.compute_community_summaries(
        collection_id="empty-col", tenant_id="default", conn=dic_conn
    )
    assert results == []
    rows = dic_conn.execute(
        "SELECT 1 FROM dic_community_summaries WHERE collection_id = ?",
        ("empty-col",),
    ).fetchall()
    assert rows == []


def test_compute_community_summaries_upsert(dic_conn):
    """Re-running on the same collection updates existing communities."""
    _insert_chunks(
        dic_conn,
        [{"id": "c1", "content": "first version of text"}],
        [{"rag_chunk_id": "c1", "doc_id": "doc-1", "collection_id": "col-up"}],
    )
    se.compute_community_summaries(
        collection_id="col-up", tenant_id="default", conn=dic_conn
    )
    # Re-run with same chunk but updated content (simulated by just re-running)
    se.compute_community_summaries(
        collection_id="col-up", tenant_id="default", conn=dic_conn
    )
    rows = dic_conn.execute(
        "SELECT community_id FROM dic_community_summaries WHERE collection_id = ?",
        ("col-up",),
    ).fetchall()
    # Should still have exactly one community (upsert, not duplicate)
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# Jaccard / term helpers
# --------------------------------------------------------------------------- #

def test_jaccard_identical():
    s = {"a", "b", "c"}
    assert se._jaccard(s, s) == 1.0


def test_jaccard_disjoint():
    assert se._jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_partial():
    assert se._jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_extract_significant_terms_filters_stopwords():
    terms = se._extract_significant_terms("The quick brown fox jumps over the lazy dog")
    assert "the" not in terms
    assert "quick" in terms
    assert "jumps" in terms
