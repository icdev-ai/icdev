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
