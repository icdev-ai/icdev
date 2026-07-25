#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the RCE RAPTOR summary hierarchy (raptor.py) — task rce-raptor-01.

Fixture/temp-DB driven: a fresh worktree's data/*.db is empty, so we build a
throwaway SQLite vector-store DB, seed leaf chunks via SQLiteVectorStore, and
drive the builder with an injected deterministic summarizer + embedder (no live
LLM). raptor-02 retrieval tests are appended to this file after that task lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.rag.raptor import RaptorBuilder, SummaryStore
from tools.rag.sqlite_vector_store import SQLiteVectorStore
from tools.rag.vector_store_provider import VectorChunk


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _fake_embedder(text: str):
    """Deterministic 8-dim embedding from the text hash — no live provider."""
    h = abs(hash(text))
    return [((h >> (i * 4)) & 0xF) / 15.0 + 0.01 for i in range(8)]


def _fake_summarizer(text: str) -> str:
    return f"SUMMARY[{len(text)}]:" + text[:20].replace("\n", " ")


def _dead_summarizer(_text: str) -> str:
    """Simulates an unavailable LLM — always returns ''."""
    return ""


@pytest.fixture
def rag_db(tmp_path) -> Path:
    """A temp SQLite vector DB seeded with leaf chunks for two source docs."""
    db_path = tmp_path / "rag_vectors.db"
    store = SQLiteVectorStore(db_path=db_path)
    chunks = []
    for doc in ("doc-A", "doc-B"):
        for idx in range(6):  # 6 leaves each → 2 groups of 3 at group_size=3
            chunks.append(
                VectorChunk(
                    chunk_id=f"{doc}-chunk-{idx}",
                    content=f"{doc} leaf content number {idx} about compliance controls",
                    embedding=_fake_embedder(f"{doc}-{idx}"),
                    source_type="compliance_artifacts",
                    source_id=doc,
                    source_table="compliance",
                    chunk_index=idx,
                    total_chunks=6,
                )
            )
    inserted = store.upsert(chunks)
    assert inserted == 12
    return db_path


def _builder(db_path, summarizer, **overrides):
    cfg = {"group_size": 3, "max_levels": 2}
    cfg.update(overrides)
    store = SummaryStore(db_path=db_path)
    return RaptorBuilder(
        store=store,
        leaf_db_path=db_path,
        summarizer=summarizer,
        embedder=_fake_embedder,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_table_created(self, tmp_path):
        db_path = tmp_path / "rag_vectors.db"
        store = SummaryStore(db_path=db_path)
        conn = store._get_conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='rag_chunk_summaries'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert store.count() == 0


# ---------------------------------------------------------------------------
# Builder — tree structure
# ---------------------------------------------------------------------------


class TestBuildHierarchy:
    def test_creates_level1_and_level2(self, rag_db):
        builder = _builder(rag_db, _fake_summarizer)
        result = builder.build_hierarchy()
        # 2 docs × 2 groups (6 leaves / group_size 3) → 4 level-1
        assert result["level1_created"] == 4
        # 1 root per doc → 2 level-2
        assert result["level2_created"] == 2
        assert result["documents_built"] == 2
        assert result["dry_run"] is False

    def test_parent_child_edges(self, rag_db):
        builder = _builder(rag_db, _fake_summarizer)
        builder.build_hierarchy(source_id="doc-A")
        store = SummaryStore(db_path=rag_db)
        conn = store._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, level, parent_chunk_id, child_ids FROM "
                "rag_chunk_summaries WHERE source_id='doc-A' ORDER BY level"
            ).fetchall()
        finally:
            conn.close()
        by_level = {}
        for rid, level, parent, child_ids in rows:
            by_level.setdefault(level, []).append((rid, parent, child_ids))
        assert len(by_level[1]) == 2  # two parent summaries
        assert len(by_level[2]) == 1  # one root
        import json as _json

        root_id, root_parent, root_children = by_level[2][0]
        assert root_parent is None  # root has no parent
        root_child_set = set(_json.loads(root_children))
        # Every level-1 summary points at the root and is a child of the root.
        for l1_id, l1_parent, l1_children in by_level[1]:
            assert l1_parent == root_id
            assert l1_id in root_child_set
            # level-1 children are leaf chunk ids (citation resolves to leaves)
            leaf_children = _json.loads(l1_children)
            assert all(c.startswith("doc-A-chunk-") for c in leaf_children)

    def test_idempotent_rebuild(self, rag_db):
        builder = _builder(rag_db, _fake_summarizer)
        builder.build_hierarchy()
        builder.build_hierarchy()  # second run must not duplicate
        store = SummaryStore(db_path=rag_db)
        assert store.count() == 6  # 4 level-1 + 2 level-2, not doubled

    def test_summaries_are_searchable(self, rag_db):
        builder = _builder(rag_db, _fake_summarizer)
        builder.build_hierarchy()
        store = SummaryStore(db_path=rag_db)
        hits = store.search(_fake_embedder("doc-A-0"), top_k=10)
        assert hits
        # summaries are tagged so retrieval can exclude them from citations
        assert all(h.metadata.get("is_summary") is True for h in hits)
        assert all("child_ids" in h.metadata for h in hits)


# ---------------------------------------------------------------------------
# Graceful no-op + dry-run
# ---------------------------------------------------------------------------


class TestGracefulAndDryRun:
    def test_noop_when_llm_unavailable(self, rag_db):
        builder = _builder(rag_db, _dead_summarizer)
        result = builder.build_hierarchy()
        assert result["level1_created"] == 0
        assert result["level2_created"] == 0
        store = SummaryStore(db_path=rag_db)
        assert store.count() == 0  # nothing persisted

    def test_dry_run_writes_nothing(self, rag_db):
        builder = _builder(rag_db, _fake_summarizer)
        result = builder.build_hierarchy(dry_run=True)
        assert result["dry_run"] is True
        assert result["level1_created"] == 4  # planned counts still reported
        store = SummaryStore(db_path=rag_db)
        assert store.count() == 0  # but nothing written


# ===========================================================================
# raptor-02: multi-level retrieval + dedup in RAGRetriever.search
# ===========================================================================

from unittest.mock import patch  # noqa: E402

from tools.rag.retriever import RAGRetriever, _merge_raptor_results  # noqa: E402
from tools.rag.vector_store_provider import SearchResult  # noqa: E402


def _leaf(chunk_id, score, source_type="compliance_artifacts"):
    return SearchResult(
        chunk_id=chunk_id, content=f"leaf {chunk_id}", source_type=source_type,
        source_id="doc-A", score=score, final_score=score,
    )


def _summary(chunk_id, score, level, child_ids):
    return SearchResult(
        chunk_id=chunk_id, content=f"summary {chunk_id}", source_type="compliance_artifacts",
        source_id="doc-A", score=score, final_score=score,
        metadata={"is_summary": True, "level": level, "child_ids": child_ids},
    )


class TestMergeRaptorResults:
    def test_empty_summaries_is_identity(self):
        leaves = [_leaf("l1", 0.9), _leaf("l2", 0.8)]
        assert _merge_raptor_results(leaves, []) == leaves

    def test_parent_dropped_when_child_leaf_present(self):
        leaves = [_leaf("l1", 0.9), _leaf("l2", 0.8)]
        # summary whose child l1 is already retrieved → dropped (prefer leaf)
        summaries = [_summary("s1", 0.95, level=1, child_ids=["l1", "lX"])]
        merged = _merge_raptor_results(leaves, summaries)
        ids = [r.chunk_id for r in merged]
        assert ids == ["l1", "l2"]  # summary dropped, leaves preferred

    def test_summary_kept_when_children_absent(self):
        leaves = [_leaf("l1", 0.9)]
        # children l7/l8 not in leaf pool → summary survives as fallback context
        summaries = [_summary("s1", 0.7, level=1, child_ids=["l7", "l8"])]
        merged = _merge_raptor_results(leaves, summaries)
        ids = [r.chunk_id for r in merged]
        assert "s1" in ids and "l1" in ids

    def test_prefers_finer_level1_over_level2_parent(self):
        leaves = []  # weak leaf retrieval
        l1 = _summary("s1", 0.6, level=1, child_ids=["l7", "l8"])
        root = _summary("s_root", 0.9, level=2, child_ids=["s1"])
        merged = _merge_raptor_results(leaves, [root, l1])
        ids = [r.chunk_id for r in merged]
        # level-1 processed first (survives); its parent root is then dropped
        assert "s1" in ids
        assert "s_root" not in ids

    def test_summaries_tagged_for_citation_exclusion(self):
        leaves = [_leaf("l1", 0.9)]
        summaries = [_summary("s1", 0.7, level=1, child_ids=["l7"])]
        merged = _merge_raptor_results(leaves, summaries)
        summ = [r for r in merged if r.chunk_id == "s1"][0]
        assert summ.metadata.get("is_summary") is True


def _retriever(enabled: bool):
    cfg = {
        "rag": {
            "raptor": {"enabled": enabled, "summary_top_k": 10},
            "retrieval": {"final_top_k": 10, "vector_top_k": 50,
                          "fusion_method": "rrf", "time_decay_enabled": False},
            "rerank": {"enabled": False},
            "provenance": {"enabled": False},
        }
    }
    return RAGRetriever(config=cfg)


class _FakeProvider:
    def embed(self, text):
        return [0.1] * 8


class _FakeStore:
    def __init__(self, leaves):
        self._leaves = leaves

    def search(self, query_embedding, top_k=50, filters=None):
        return list(self._leaves)


class TestRetrieverRaptorIntegration:
    def test_disabled_is_old_path(self):
        r = _retriever(enabled=False)
        leaves = [_leaf("l1", 0.9), _leaf("l2", 0.8)]
        with patch("tools.rag.retriever._get_embedding_provider", return_value=_FakeProvider()), \
             patch("tools.rag.retriever.VectorStoreFactory.create", return_value=_FakeStore(leaves)), \
             patch.object(RAGRetriever, "_search_summaries") as mock_sum:
            out = r.search("q", top_k=10)
        # summary tier is never consulted when disabled → byte-for-byte old path
        mock_sum.assert_not_called()
        assert {x.chunk_id for x in out} == {"l1", "l2"}

    def test_enabled_merges_summary_fallback(self):
        r = _retriever(enabled=True)
        leaves = [_leaf("l1", 0.9)]
        summ = [_summary("s1", 0.7, level=1, child_ids=["l7", "l8"])]
        with patch("tools.rag.retriever._get_embedding_provider", return_value=_FakeProvider()), \
             patch("tools.rag.retriever.VectorStoreFactory.create", return_value=_FakeStore(leaves)), \
             patch.object(RAGRetriever, "_search_summaries", return_value=summ):
            out = r.search("q", top_k=10)
        ids = {x.chunk_id for x in out}
        assert "l1" in ids and "s1" in ids  # summary rescues weak leaf retrieval

    def test_enabled_dedups_parent_when_leaf_present(self):
        r = _retriever(enabled=True)
        leaves = [_leaf("l1", 0.9)]
        summ = [_summary("s1", 0.95, level=1, child_ids=["l1"])]  # child == retrieved leaf
        with patch("tools.rag.retriever._get_embedding_provider", return_value=_FakeProvider()), \
             patch("tools.rag.retriever.VectorStoreFactory.create", return_value=_FakeStore(leaves)), \
             patch.object(RAGRetriever, "_search_summaries", return_value=summ):
            out = r.search("q", top_k=10)
        ids = {x.chunk_id for x in out}
        assert "l1" in ids and "s1" not in ids  # parent summary deduped away

    def test_enabled_summary_search_failure_is_safe(self):
        """A missing summary table must not break retrieval (best-effort [])."""
        r = _retriever(enabled=True)
        leaves = [_leaf("l1", 0.9)]
        # real _search_summaries runs against an (absent) summary table → []
        with patch("tools.rag.retriever._get_embedding_provider", return_value=_FakeProvider()), \
             patch("tools.rag.retriever.VectorStoreFactory.create", return_value=_FakeStore(leaves)):
            out = r.search("q", top_k=10)
        assert {x.chunk_id for x in out} == {"l1"}
