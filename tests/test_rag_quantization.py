#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for SQLite vector-store quantization (rce-quant-01).

Covers the self-describing blob header (float16/float32), legacy headerless
float32 back-compat, config-driven write dtype, migrate_tier warm round-trip,
and cosine preservation under float16. Pure-Python / temp-DB driven.
"""

from __future__ import annotations

import struct

from tools.rag.sqlite_vector_store import (
    SQLiteVectorStore,
    _BLOB_MAGIC,
    _blob_to_embedding,
    _cosine_similarity,
    _embedding_to_blob,
    _resolve_sqlite_dtype,
)
from tools.rag.vector_store_provider import VectorChunk


def _make_chunk(content, chunk_id, embedding, tier="hot"):
    c = VectorChunk(
        chunk_id=chunk_id,
        content=content,
        source_type="test",
        source_id="1",
        source_table="test_table",
        embedding=embedding,
        tier=tier,
        classification="CUI",
    )
    c.compute_content_hash()
    return c


# ---- Blob header round-trip ----


class TestBlobHeader:
    def test_float16_header_roundtrip(self):
        emb = [0.1, 0.2, 0.3, 0.4, 0.5]
        blob = _embedding_to_blob(emb, dtype="float16")
        assert blob[:4] == _BLOB_MAGIC
        assert blob[4:5] == b"e"
        restored = _blob_to_embedding(blob)
        assert len(restored) == len(emb)
        for a, b in zip(emb, restored):
            assert abs(a - b) < 2e-3  # float16 precision

    def test_float32_header_roundtrip_exact(self):
        emb = [0.1, 0.2, 0.3, 0.4, 0.5]
        blob = _embedding_to_blob(emb, dtype="float32")
        assert blob[:4] == _BLOB_MAGIC
        assert blob[4:5] == b"f"
        restored = _blob_to_embedding(blob)
        for a, b in zip(emb, restored):
            assert abs(a - b) < 1e-6  # float32 is exact for these values

    def test_legacy_headerless_float32_read_exact(self):
        # A blob written before this format existed: raw float32, no header.
        emb = [0.1, 0.2, 0.3, 0.4, 0.5]
        legacy_blob = struct.pack(f"{len(emb)}f", *emb)
        assert legacy_blob[:4] != _BLOB_MAGIC
        restored = _blob_to_embedding(legacy_blob)
        assert len(restored) == len(emb)
        for a, b in zip(emb, restored):
            assert abs(a - b) < 1e-6

    def test_float16_blob_is_smaller_than_float32(self):
        emb = [0.01 * i for i in range(128)]
        f16 = _embedding_to_blob(emb, dtype="float16")
        f32 = _embedding_to_blob(emb, dtype="float32")
        # payload: 2 bytes/val vs 4 bytes/val (+ identical 5-byte header)
        assert len(f16) < len(f32)
        assert len(f16) == 5 + 2 * len(emb)
        assert len(f32) == 5 + 4 * len(emb)

    def test_default_dtype_is_float16(self):
        blob = _embedding_to_blob([0.1, 0.2, 0.3])
        assert blob[4:5] == b"e"


# ---- Cosine preservation (retrieval order) ----


class TestCosinePreservation:
    def test_cosine_float16_close_to_float32(self):
        import random

        random.seed(7)
        for _ in range(5):
            orig = [random.uniform(-1.0, 1.0) for _ in range(64)]
            rt = _blob_to_embedding(_embedding_to_blob(orig, dtype="float16"))
            query = [random.uniform(-1.0, 1.0) for _ in range(64)]
            s_full = _cosine_similarity(query, orig)
            s_f16 = _cosine_similarity(query, rt)
            assert abs(s_full - s_f16) < 1e-2

    def test_float16_preserves_ranking(self):
        query = [1.0, 0.0, 0.0, 0.0]
        vecs = {
            "a": [0.99, 0.10, 0.0, 0.0],
            "b": [0.50, 0.50, 0.50, 0.50],
            "c": [0.0, 1.0, 0.0, 0.0],
        }
        full_order = sorted(vecs, key=lambda k: _cosine_similarity(query, vecs[k]), reverse=True)
        f16_order = sorted(
            vecs,
            key=lambda k: _cosine_similarity(query, _blob_to_embedding(_embedding_to_blob(vecs[k], "float16"))),
            reverse=True,
        )
        assert full_order == f16_order


# ---- Config selects write dtype ----


class TestConfigDtype:
    def test_resolve_default(self):
        assert _resolve_sqlite_dtype({}) == "float16"

    def test_resolve_from_nested_rag(self):
        cfg = {"rag": {"quantization": {"sqlite_dtype": "float32"}}}
        assert _resolve_sqlite_dtype(cfg) == "float32"

    def test_resolve_from_direct_block(self):
        cfg = {"quantization": {"sqlite_dtype": "float32"}}
        assert _resolve_sqlite_dtype(cfg) == "float32"

    def test_resolve_invalid_falls_back(self):
        cfg = {"quantization": {"sqlite_dtype": "int8"}}
        assert _resolve_sqlite_dtype(cfg) == "float16"

    def test_store_writes_configured_dtype(self, tmp_path):
        emb = [0.1, 0.2, 0.3, 0.4]
        # float32-configured store
        store32 = SQLiteVectorStore(
            db_path=tmp_path / "f32.db",
            config={"rag": {"quantization": {"sqlite_dtype": "float32"}}},
        )
        store32.upsert([_make_chunk("c", "c1", emb)])
        conn = store32._get_conn()
        blob32 = conn.execute("SELECT embedding FROM rag_chunks WHERE id='c1'").fetchone()[0]
        conn.close()
        assert bytes(blob32)[4:5] == b"f"

        # default (float16) store
        store16 = SQLiteVectorStore(db_path=tmp_path / "f16.db")
        store16.upsert([_make_chunk("c", "c1", emb)])
        conn = store16._get_conn()
        blob16 = conn.execute("SELECT embedding FROM rag_chunks WHERE id='c1'").fetchone()[0]
        conn.close()
        assert bytes(blob16)[4:5] == b"e"
        assert len(bytes(blob16)) < len(bytes(blob32))


# ---- Store round-trip and migrate_tier warm ----


class TestStoreRoundTrip:
    def test_search_roundtrip_float16(self, tmp_path):
        store = SQLiteVectorStore(db_path=tmp_path / "rt.db")
        store.upsert(
            [
                _make_chunk("alpha", "c1", [1.0, 0.0, 0.0]),
                _make_chunk("beta", "c2", [0.0, 1.0, 0.0]),
            ]
        )
        results = store.search([1.0, 0.0, 0.0], top_k=10)
        assert len(results) == 2
        assert results[0].chunk_id == "c1"
        assert results[0].score > results[1].score

    def test_migrate_tier_warm_roundtrips(self, tmp_path):
        # The former warm path wrote headerless np.float16.tobytes() which was
        # then MIS-read as float32. After the header fix it must round-trip.
        store = SQLiteVectorStore(db_path=tmp_path / "warm.db")
        emb = [0.11, 0.22, 0.33, 0.44, 0.55, 0.66]
        store.upsert([_make_chunk("warm me", "c1", emb)])
        assert store.migrate_tier(["c1"], "warm") == 1

        conn = store._get_conn()
        blob = conn.execute("SELECT embedding, tier FROM rag_chunks WHERE id='c1'").fetchone()
        conn.close()
        assert blob[1] == "warm"
        assert bytes(blob[0])[:4] == _BLOB_MAGIC
        assert bytes(blob[0])[4:5] == b"e"  # float16
        restored = _blob_to_embedding(bytes(blob[0]))
        assert len(restored) == len(emb)
        for a, b in zip(emb, restored):
            assert abs(a - b) < 2e-3

    def test_migrate_tier_warm_search_still_ranks(self, tmp_path):
        store = SQLiteVectorStore(db_path=tmp_path / "warm2.db")
        store.upsert(
            [
                _make_chunk("alpha", "c1", [1.0, 0.0, 0.0]),
                _make_chunk("beta", "c2", [0.0, 1.0, 0.0]),
            ]
        )
        store.migrate_tier(["c1", "c2"], "warm")
        results = store.search([1.0, 0.0, 0.0], top_k=10)
        assert results[0].chunk_id == "c1"

    def test_mixed_dtype_store(self, tmp_path):
        # A store holding a legacy float32 row AND new float16 rows must search.
        store = SQLiteVectorStore(db_path=tmp_path / "mixed.db")
        # New float16 rows via normal upsert.
        store.upsert([_make_chunk("new16", "c1", [0.0, 1.0, 0.0])])
        # Inject a legacy headerless float32 row directly.
        legacy = struct.pack("3f", 1.0, 0.0, 0.0)
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO rag_chunks (id, content, content_hash, embedding, "
            "source_type, source_id) VALUES (?,?,?,?,?,?)",
            ("legacy1", "legacy", "hash-legacy", legacy, "test", "1"),
        )
        conn.commit()
        conn.close()
        results = store.search([1.0, 0.0, 0.0], top_k=10)
        assert results[0].chunk_id == "legacy1"  # exact match to legacy vector
        assert len(results) == 2
