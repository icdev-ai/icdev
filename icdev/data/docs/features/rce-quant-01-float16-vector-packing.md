# CUI // SP-CTI

# rce-quant-01 — float16 vector packing with float32 back-compat

## Summary

Evolves the SQLite vector store (`tools/rag/sqlite_vector_store.py`) to pack
embeddings at **float16** by default — roughly halving embedding storage and
IO — while keeping every previously stored row readable. No new dependencies:
Python's `struct` supports half-precision natively via the `'e'` format, so
this is fully air-gap safe (numpy is no longer required on the warm-tier path).

This is the deliberate zero-dependency alternative to TurboQuant, which was
evaluated and skipped (`C:\AI\searches\rag_alt.md`).

## Self-describing blob format

Blobs are now versioned and self-describing:

```
+--------+-----------+------------------------+
| 'RVQ1' | dtype (1) | packed payload         |
| 4 byte | 'f' | 'e' | struct.pack(n{f|e})    |
+--------+-----------+------------------------+
```

- `dtype` byte: `f` = float32 (exact), `e` = float16 (~2e-3 element error).
- `_embedding_to_blob(embedding, dtype="float16")` writes header + payload.
- `_blob_to_embedding(blob)` reads the dtype from the header. **Blobs without
  the `RVQ1` magic are treated as legacy raw float32** (`len // 4` values), so
  all pre-existing rows and PostgreSQL BYTEA-fallback blobs keep working — no
  forced re-index.

### Magic-collision risk (negligible)

A legacy float32 blob is only misread if its first four little-endian bytes
spell `b'RVQ1'`, i.e. `element[0]` equals a value on the order of `1e-9`.
Normalized embeddings never produce it.

## Configuration

`args/rag_config.yaml`:

```yaml
rag:
  quantization:
    sqlite_dtype: float16   # or float32 for exact storage
```

Write dtype is resolved once per store (`_resolve_sqlite_dtype`). **Reads are
always back-compat** regardless of this setting.

## Retrieval correctness

- Cosine similarity is computed in float32 after the cast.
- float16 round-trip vs original float32 stays within `1e-2` cosine — retrieval
  order is preserved (see `tests/test_rag_quantization.py::TestCosinePreservation`).

## warm-tier bug fix

`migrate_tier(..., "warm")` previously wrote `np.array(emb, float16).tobytes()`
with **no header**, which `_blob_to_embedding` then mis-read as float32
(garbage vectors). It now uses `_embedding_to_blob(emb, dtype="float16")`, so
warm-tier blobs round-trip correctly.

## Micro-benchmark (768-dim, 2000 vectors)

| Metric | float32 (legacy) | float16 (new) | Delta |
|---|---|---|---|
| Embedding bytes / vector | 3077 | 1541 | -49.9% |
| Total embedding payload | 6.15 MB | 3.08 MB | -49.9% |
| SQLite DB file size | 8.50 MB | 4.39 MB | -48.4% |
| Brute-force query latency (top-10) | 152.8 ms | 156.4 ms | within noise |

Query latency is dominated by the Python cosine loop, not blob size, so it is
unchanged within measurement noise; the win is storage/IO.

## Files

- `tools/rag/sqlite_vector_store.py` (+ `icdev/` mirror) — format, config, warm fix
- `tools/dashboard/api/rag_kg_search.py` (+ `icdev/` mirror) — header-aware reader
- `args/rag_config.yaml` — `rag.quantization.sqlite_dtype`
- `tests/test_rag_quantization.py` — new
- `tests/test_rag_vector_stores.py` — `test_roundtrip_embedding` tolerance → float16
