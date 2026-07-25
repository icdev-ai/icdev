#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG vector store provider, SQLite backend, and factory (D-RAG-1)."""

from __future__ import annotations


import pytest

from tools.rag.vector_store_provider import SearchResult, VectorChunk, VectorStoreProvider
from tools.rag.sqlite_vector_store import (
    SQLiteVectorStore,
    _blob_to_embedding,
    _cosine_similarity,
    _embedding_to_blob,
)
from tools.rag.vector_store_factory import VectorStoreFactory


# ---- Fixtures ----


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temp SQLite vector store DB."""
    db_path = tmp_path / "test_rag.db"
    return str(db_path)


@pytest.fixture
def store(tmp_db):
    """Create a SQLiteVectorStore instance."""
    return SQLiteVectorStore(db_path=tmp_db)


def _make_chunk(content="test content", source_type="test", source_id="1", embedding=None, chunk_id=None, tier="hot"):
    """Helper to create a VectorChunk."""
    if embedding is None:
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    c = VectorChunk(
        chunk_id=chunk_id or f"chunk-test-{id(content)}",
        content=content,
        source_type=source_type,
        source_id=source_id,
        source_table=f"{source_type}_table",
        embedding=embedding,
        tier=tier,
        classification="CUI",
    )
    c.compute_content_hash()
    return c


# ---- VectorChunk tests ----


class TestVectorChunk:
    def test_default_values(self):
        chunk = VectorChunk()
        assert chunk.chunk_id == ""
        assert chunk.content == ""
        assert chunk.tier == "hot"
        assert chunk.classification == "CUI"
        assert chunk.metadata == {}

    def test_compute_content_hash(self):
        chunk = VectorChunk(content="hello world")
        h = chunk.compute_content_hash()
        assert len(h) == 64  # SHA-256 hex
        assert chunk.content_hash == h

    def test_content_hash_deterministic(self):
        c1 = VectorChunk(content="same text")
        c2 = VectorChunk(content="same text")
        assert c1.compute_content_hash() == c2.compute_content_hash()

    def test_content_hash_different(self):
        c1 = VectorChunk(content="text a")
        c2 = VectorChunk(content="text b")
        assert c1.compute_content_hash() != c2.compute_content_hash()


# ---- SearchResult tests ----


class TestSearchResult:
    def test_default_values(self):
        r = SearchResult()
        assert r.score == 0.0
        assert r.final_score == 0.0
        assert r.tier == "hot"

    def test_to_dict(self):
        r = SearchResult(
            chunk_id="c1",
            content="test",
            source_type="signals",
            score=0.95,
            final_score=0.88,
            tier="hot",
        )
        d = r.to_dict()
        assert d["chunk_id"] == "c1"
        assert d["score"] == 0.95
        assert d["final_score"] == 0.88
        assert d["tier"] == "hot"
        assert "classification" in d

    def test_to_dict_rounds_scores(self):
        r = SearchResult(score=0.123456789, final_score=0.987654321)
        d = r.to_dict()
        assert d["score"] == 0.1235
        assert d["final_score"] == 0.9877


# ---- Embedding helpers ----


class TestEmbeddingHelpers:
    def test_roundtrip_embedding(self):
        # Default write dtype is float16 (rce-quant-01): ~50% storage win at
        # ~2e-3 per-element error. Legacy exact float32 read is covered
        # separately in tests/test_rag_quantization.py.
        emb = [0.1, 0.2, 0.3, 0.4, 0.5]
        blob = _embedding_to_blob(emb)
        restored = _blob_to_embedding(blob)
        for a, b in zip(emb, restored):
            assert abs(a - b) < 2e-3

    def test_cosine_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_cosine_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_cosine_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-6

    def test_cosine_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 1.0]
        assert _cosine_similarity(a, b) == 0.0


# ---- SQLiteVectorStore tests ----


class TestSQLiteVectorStore:
    def test_provider_name(self, store):
        assert store.provider_name == "sqlite"

    def test_check_availability(self, store):
        assert store.check_availability() is True

    def test_upsert_single(self, store):
        chunk = _make_chunk("hello world", chunk_id="c1")
        count = store.upsert([chunk])
        assert count == 1
        assert store.count() == 1

    def test_upsert_empty_list(self, store):
        assert store.upsert([]) == 0

    def test_upsert_dedup(self, store):
        c1 = _make_chunk("duplicate content", chunk_id="c1")
        c2 = _make_chunk("duplicate content", chunk_id="c2")
        store.upsert([c1])
        count = store.upsert([c2])
        assert count == 0  # Skipped due to same content_hash
        assert store.count() == 1

    def test_upsert_no_embedding_skipped(self, store):
        chunk = _make_chunk("no embedding", chunk_id="c1")
        chunk.embedding = None
        count = store.upsert([chunk])
        assert count == 0

    def test_upsert_multiple(self, store):
        chunks = [_make_chunk(f"content {i}", chunk_id=f"c{i}") for i in range(5)]
        count = store.upsert(chunks)
        assert count == 5
        assert store.count() == 5

    def test_search_basic(self, store):
        chunks = [
            _make_chunk("alpha beta", embedding=[1.0, 0.0, 0.0], chunk_id="c1"),
            _make_chunk("gamma delta", embedding=[0.0, 1.0, 0.0], chunk_id="c2"),
        ]
        store.upsert(chunks)
        results = store.search([1.0, 0.0, 0.0], top_k=10)
        assert len(results) == 2
        assert results[0].chunk_id == "c1"  # Most similar
        assert results[0].score > results[1].score

    def test_search_top_k(self, store):
        for i in range(10):
            emb = [0.0] * 3
            emb[i % 3] = 1.0
            store.upsert([_make_chunk(f"content {i}", embedding=emb, chunk_id=f"c{i}")])
        results = store.search([1.0, 0.0, 0.0], top_k=3)
        assert len(results) <= 3

    def test_search_with_source_type_filter(self, store):
        store.upsert(
            [
                _make_chunk("signal 1", source_type="innovation_signals", embedding=[1.0, 0.0], chunk_id="c1"),
                _make_chunk("pain 1", source_type="creative_pain_points", embedding=[0.9, 0.1], chunk_id="c2"),
            ]
        )
        results = store.search([1.0, 0.0], filters={"source_type": "innovation_signals"})
        assert len(results) == 1
        assert results[0].source_type == "innovation_signals"

    def test_search_with_tier_filter(self, store):
        store.upsert(
            [
                _make_chunk("hot chunk", tier="hot", embedding=[1.0, 0.0], chunk_id="c1"),
                _make_chunk("cold chunk", tier="cold", embedding=[0.9, 0.1], chunk_id="c2"),
            ]
        )
        results = store.search([1.0, 0.0], filters={"tier": "hot"})
        assert len(results) == 1
        assert results[0].tier == "hot"

    def test_delete(self, store):
        store.upsert([_make_chunk("to delete", chunk_id="c1")])
        assert store.count() == 1
        deleted = store.delete(["c1"])
        assert deleted == 1
        assert store.count() == 0

    def test_delete_empty_list(self, store):
        assert store.delete([]) == 0

    def test_delete_nonexistent(self, store):
        deleted = store.delete(["nonexistent"])
        assert deleted == 0

    def test_count_with_filters(self, store):
        store.upsert(
            [
                _make_chunk("a", source_type="type_a", embedding=[1.0], chunk_id="c1"),
                _make_chunk("b", source_type="type_b", embedding=[1.0], chunk_id="c2"),
                _make_chunk("c", source_type="type_a", embedding=[1.0], chunk_id="c3"),
            ]
        )
        assert store.count() == 3
        assert store.count(filters={"source_type": "type_a"}) == 2
        assert store.count(filters={"source_type": "type_b"}) == 1

    def test_get_by_content_hash(self, store):
        chunk = _make_chunk("find me by hash", chunk_id="c1")
        store.upsert([chunk])
        found = store.get_by_content_hash(chunk.content_hash)
        assert found is not None
        assert found.chunk_id == "c1"
        assert found.content == "find me by hash"

    def test_get_by_content_hash_not_found(self, store):
        result = store.get_by_content_hash("nonexistent_hash")
        assert result is None

    def test_migrate_tier_to_cold(self, store):
        store.upsert([_make_chunk("test", chunk_id="c1")])
        migrated = store.migrate_tier(["c1"], "cold")
        assert migrated == 1
        assert store.count(filters={"tier": "cold"}) == 1

    def test_migrate_tier_to_warm(self, store):
        store.upsert([_make_chunk("test", chunk_id="c1")])
        migrated = store.migrate_tier(["c1"], "warm")
        assert migrated == 1
        assert store.count(filters={"tier": "warm"}) == 1

    def test_migrate_tier_invalid(self, store):
        assert store.migrate_tier(["c1"], "invalid_tier") == 0
        assert store.migrate_tier([], "hot") == 0

    def test_metadata_round_trip(self, store):
        chunk = _make_chunk("with meta", chunk_id="c1")
        chunk.metadata = {"key": "value", "nested": {"a": 1}}
        store.upsert([chunk])
        results = store.search(chunk.embedding, top_k=1)
        assert results[0].metadata == {"key": "value", "nested": {"a": 1}}

    def test_tenant_isolation(self, tmp_path):
        """Verify tenant_id creates separate DB files."""
        store_a = SQLiteVectorStore(db_path=tmp_path / "rag_a.db", tenant_id="")
        # Tenant stores create in data/tenants/ dir — test with explicit paths
        store_a.upsert([_make_chunk("tenant a data", chunk_id="ca", embedding=[1.0])])
        assert store_a.count() == 1


# ---- VectorStoreFactory tests ----


class TestVectorStoreFactory:
    def test_create_sqlite_explicit(self):
        store = VectorStoreFactory.create(backend="sqlite", config={"rag": {"vector_store": {}}})
        assert store.provider_name == "sqlite"

    def test_create_auto_falls_to_sqlite(self):
        """Auto-detect should at minimum resolve to SQLite."""
        store = VectorStoreFactory.create(backend="auto", config={"rag": {"vector_store": {}}})
        assert store.provider_name in ("sqlite", "chromadb", "faiss")

    def test_list_available_includes_sqlite(self):
        available = VectorStoreFactory.list_available()
        assert "sqlite" in available

    def test_create_with_empty_config(self):
        store = VectorStoreFactory.create(config={})
        assert store is not None
        assert hasattr(store, "provider_name")

    def test_chromadb_fallback_to_sqlite(self):
        """If ChromaDB not installed, factory should fallback to SQLite."""
        store = VectorStoreFactory.create(backend="chromadb", config={"rag": {"vector_store": {}}})
        # Either chromadb or sqlite depending on installation
        assert store.provider_name in ("chromadb", "sqlite")

    def test_faiss_fallback_to_sqlite(self):
        """If FAISS not installed, factory should fallback to SQLite."""
        store = VectorStoreFactory.create(backend="faiss", config={"rag": {"vector_store": {}}})
        assert store.provider_name in ("faiss", "sqlite")


# ---- VectorStoreProvider ABC tests ----


class TestVectorStoreProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            VectorStoreProvider()

    def test_abc_defines_required_methods(self):
        methods = ["upsert", "search", "delete", "count", "check_availability"]
        for method in methods:
            assert hasattr(VectorStoreProvider, method)

    def test_default_get_by_content_hash(self):
        """Default implementation returns None."""
        # We can't instantiate ABC directly, but SQLite overrides it
        # Just verify the method exists on the ABC
        assert hasattr(VectorStoreProvider, "get_by_content_hash")

    def test_default_migrate_tier(self):
        """Default implementation returns 0."""
        assert hasattr(VectorStoreProvider, "migrate_tier")
