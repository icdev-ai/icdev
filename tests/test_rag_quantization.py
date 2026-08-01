#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for SQLite vector-store quantization (rce-quant-01, rce-quant-02).

Covers the self-describing blob header (float16/float32), legacy headerless
float32 back-compat, config-driven write dtype, migrate_tier warm round-trip,
cosine preservation under float16, and the optional binary-quantization
Hamming pre-filter. Pure-Python / temp-DB driven.
"""

from __future__ import annotations

import struct

from tools.rag.sqlite_vector_store import (
    SQLiteVectorStore,
    _BLOB_MAGIC,
    _blob_to_embedding,
    _cosine_similarity,
    _embedding_to_blob,
    _embedding_to_sign_bits,
    _hamming_distance,
    _resolve_binary_prefilter,
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


# ===========================================================================
# rce-quant-02 — binary quantization + Hamming pre-filter
# ===========================================================================


def _db_row(cid, emb, sign="auto"):
    """Build a rag_chunks SELECT-shaped row as search() consumes it.

    Column order matches search():
    (id, content, source_type, source_id, source_table, chunk_index,
     embedding, metadata, tier, classification, sign_bits)
    """
    if sign == "auto":
        sign = _embedding_to_sign_bits(emb)
    return (
        cid, "content", "test", "1", "test_table", 0,
        _embedding_to_blob(emb), "{}", "hot", "CUI", sign,
    )


class TestBinaryHelpers:
    def test_sign_bits_packing(self):
        emb = [0.5, -0.5, 0.1, -0.2, 3.0, -1.0, 0.0, -0.9]  # >=0 -> 1
        bits = _embedding_to_sign_bits(emb)
        assert len(bits) == 1  # 8 dims -> 1 byte
        # 1,0,1,0,1,0,1,0 MSB-first = 0b10101010 = 0xAA
        assert bits[0] == 0xAA

    def test_sign_bits_multibyte_length(self):
        emb = [0.1] * 20  # ceil(20/8) = 3 bytes
        assert len(_embedding_to_sign_bits(emb)) == 3

    def test_hamming_distance_basic(self):
        a = _embedding_to_sign_bits([1.0, 1.0, 1.0, 1.0])
        b = _embedding_to_sign_bits([1.0, -1.0, 1.0, -1.0])
        assert _hamming_distance(a, a) == 0
        assert _hamming_distance(a, b) == 2

    def test_resolve_binary_prefilter_default_off(self):
        cfg = _resolve_binary_prefilter({})
        assert cfg["enabled"] is False
        assert cfg["candidate_multiplier"] >= 1
        assert cfg["min_corpus_size"] >= 1

    def test_resolve_binary_prefilter_from_config(self):
        cfg = _resolve_binary_prefilter(
            {"rag": {"quantization": {"binary_prefilter": {
                "enabled": True, "candidate_multiplier": 8, "min_corpus_size": 100}}}}
        )
        assert cfg["enabled"] is True
        assert cfg["candidate_multiplier"] == 8
        assert cfg["min_corpus_size"] == 100


class TestPrefilterRowSelection:
    """Directly exercise _binary_prefilter_rows on synthetic DB-shaped rows."""

    def _store(self, tmp_path, enabled, mult=4, min_corpus=10):
        return SQLiteVectorStore(
            db_path=tmp_path / "pf.db",
            config={"rag": {"quantization": {"binary_prefilter": {
                "enabled": enabled,
                "candidate_multiplier": mult,
                "min_corpus_size": min_corpus,
            }}}},
        )

    def test_disabled_returns_all(self, tmp_path):
        store = self._store(tmp_path, enabled=False)
        rows = [_db_row(f"c{i}", [1.0, -1.0, 1.0]) for i in range(50)]
        assert store._binary_prefilter_rows([1.0, -1.0, 1.0], rows, top_k=5) == rows

    def test_below_threshold_returns_all(self, tmp_path):
        store = self._store(tmp_path, enabled=True, min_corpus=200)
        rows = [_db_row(f"c{i}", [1.0, -1.0, 1.0]) for i in range(50)]
        out = store._binary_prefilter_rows([1.0, -1.0, 1.0], rows, top_k=5)
        assert out == rows

    def test_reduces_to_candidate_budget(self, tmp_path):
        store = self._store(tmp_path, enabled=True, mult=4, min_corpus=10)
        query = [1.0, 1.0, 1.0, 1.0]
        # 8 "near" rows share the query sign pattern (Hamming 0); 92 "far" rows
        # invert it (Hamming 4).
        near = [_db_row(f"near{i}", [1.0, 1.0, 1.0, 1.0]) for i in range(8)]
        far = [_db_row(f"far{i}", [-1.0, -1.0, -1.0, -1.0]) for i in range(92)]
        out = store._binary_prefilter_rows(query, near + far, top_k=2)
        assert len(out) == 8  # top_k(2) * mult(4)
        # All 8 kept rows must be the near ones (lowest Hamming).
        assert {r[0] for r in out} == {f"near{i}" for i in range(8)}

    def test_legacy_null_sign_derived_on_the_fly(self, tmp_path):
        store = self._store(tmp_path, enabled=True, mult=2, min_corpus=5)
        query = [1.0, 1.0, 1.0, 1.0]
        near = [_db_row(f"near{i}", [1.0, 1.0, 1.0, 1.0], sign=None) for i in range(4)]
        far = [_db_row(f"far{i}", [-1.0, -1.0, -1.0, -1.0], sign=None) for i in range(20)]
        out = store._binary_prefilter_rows(query, near + far, top_k=2)
        assert len(out) == 4
        assert {r[0] for r in out} == {f"near{i}" for i in range(4)}


class TestPrefilterSearchParity:
    """End-to-end: enabled pre-filter returns the same top-k as full cosine."""

    def _build_corpus(self, tmp_path, cfg):
        import random

        random.seed(11)
        store = SQLiteVectorStore(db_path=tmp_path / "corpus.db", config=cfg)
        dim = 32
        # Query sign pattern (fixed +/- per dim).
        signs = [1.0 if random.random() > 0.5 else -1.0 for _ in range(dim)]
        query = [s * 1.0 for s in signs]
        chunks = []
        # 12 "signal" vectors keep the query signs (magnitudes vary -> varying
        # cosine) so they are both Hamming-nearest AND cosine-nearest.
        for i in range(12):
            emb = [s * (1.0 + random.random()) for s in signs]
            chunks.append(_make_chunk(f"sig{i}", f"sig{i}", emb))
        # 588 "noise" vectors with independent random signs (~half match).
        for i in range(588):
            emb = [random.uniform(-1.0, 1.0) for _ in range(dim)]
            chunks.append(_make_chunk(f"noise{i}", f"noise{i}", emb))
        store.upsert(chunks)
        return store, query

    def test_enabled_matches_disabled_top_k(self, tmp_path):
        cfg = {"rag": {"quantization": {"binary_prefilter": {
            "enabled": True, "candidate_multiplier": 4, "min_corpus_size": 50}}}}
        store, query = self._build_corpus(tmp_path, cfg)

        enabled = [r.chunk_id for r in store.search(query, top_k=5)]
        # Flip prefilter off on the same store/data and re-query.
        store._binary_prefilter["enabled"] = False
        disabled = [r.chunk_id for r in store.search(query, top_k=5)]

        assert enabled == disabled  # identical top-5 ordering
        assert len(enabled) == 5

    def test_default_store_prefilter_off(self, tmp_path):
        # No config -> prefilter disabled -> plain brute force.
        store = SQLiteVectorStore(db_path=tmp_path / "def.db")
        assert store._binary_prefilter["enabled"] is False
        store.upsert(
            [
                _make_chunk("a", "c1", [1.0, 0.0, 0.0]),
                _make_chunk("b", "c2", [0.0, 1.0, 0.0]),
            ]
        )
        results = store.search([1.0, 0.0, 0.0], top_k=2)
        assert results[0].chunk_id == "c1"
