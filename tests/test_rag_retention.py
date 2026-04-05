#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG retention manager (D-RAG-6)."""

from __future__ import annotations


from tools.rag.retention_manager import (
    get_retention_status,
    get_migration_candidates,
)
from tools.rag.sqlite_vector_store import SQLiteVectorStore
from tools.rag.vector_store_provider import VectorChunk


def _make_chunk(content, chunk_id, embedding=None, tier="hot"):
    """Helper to create a VectorChunk."""
    if embedding is None:
        embedding = [0.1, 0.2, 0.3, 0.4]
    c = VectorChunk(
        chunk_id=chunk_id,
        content=content,
        source_type="test",
        source_id="1",
        embedding=embedding,
        tier=tier,
    )
    c.compute_content_hash()
    return c


class TestRetentionFunctions:
    def test_import(self):
        from tools.rag import retention_manager

        assert retention_manager is not None

    def test_get_retention_status(self):
        status = get_retention_status()
        assert isinstance(status, dict)
        assert "by_tier" in status

    def test_get_migration_candidates(self):
        candidates = get_migration_candidates()
        assert isinstance(candidates, dict)


class TestTierMigrationViaStore:
    def test_tier_migration_cold(self, tmp_path):
        """Test migrating chunks to cold tier (removes embeddings)."""
        db_path = tmp_path / "retention_test.db"
        store = SQLiteVectorStore(db_path=db_path)
        store.upsert([_make_chunk("hot data", "c1")])

        migrated = store.migrate_tier(["c1"], "cold")
        assert migrated == 1
        assert store.count(filters={"tier": "cold"}) == 1

    def test_tier_migration_warm(self, tmp_path):
        """Test migrating chunks to warm tier (float16 compression)."""
        db_path = tmp_path / "retention_test.db"
        store = SQLiteVectorStore(db_path=db_path)
        store.upsert([_make_chunk("hot data", "c1")])

        migrated = store.migrate_tier(["c1"], "warm")
        assert migrated == 1
        assert store.count(filters={"tier": "warm"}) == 1

    def test_tier_migration_preserves_content(self, tmp_path):
        """Migrating tier should preserve content text."""
        db_path = tmp_path / "retention_test.db"
        store = SQLiteVectorStore(db_path=db_path)
        store.upsert([_make_chunk("preserved content", "c1")])

        store.migrate_tier(["c1"], "cold")
        original = _make_chunk("preserved content", "c1")
        found = store.get_by_content_hash(original.content_hash)
        assert found is not None
        assert found.content == "preserved content"
        assert found.tier == "cold"

    def test_migration_invalid_tier(self, tmp_path):
        db_path = tmp_path / "test.db"
        store = SQLiteVectorStore(db_path=db_path)
        store.upsert([_make_chunk("test", "c1")])
        assert store.migrate_tier(["c1"], "invalid") == 0

    def test_migration_empty_list(self, tmp_path):
        db_path = tmp_path / "test.db"
        store = SQLiteVectorStore(db_path=db_path)
        assert store.migrate_tier([], "cold") == 0
