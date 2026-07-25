# CUI // SP-CTI

# rce-quant-02 — optional binary quantization + Hamming pre-filter

## Summary

Adds an **optional** second quantization tier to the SQLite vector store for
large air-gap corpora. Alongside each float embedding the store persists a
**1-bit-per-dimension sign vector** (packed into bytes). When enabled, search
uses a cheap **Hamming distance** on the packed sign bits to pre-select
`top_k * candidate_multiplier` candidates, then **re-ranks only those
candidates with full-precision cosine**. This shrinks the expensive
float-cosine set while preserving recall on structured embeddings.

Pure Python (`int.from_bytes` XOR + `int.bit_count`), **zero new dependencies**,
air-gap safe. **Default OFF** — search behaviour is unchanged unless enabled.

## Design

- `_embedding_to_sign_bits(embedding)` → packs sign bits (`v >= 0 → 1`),
  MSB-first, `ceil(dim/8)` bytes.
- `_hamming_distance(a, b)` → `(int.from_bytes(a) ^ int.from_bytes(b)).bit_count()`.
- Persisted in a new **nullable** `rag_chunks.sign_bits BLOB` column
  (`CREATE TABLE` for fresh DBs + guarded `ALTER TABLE ADD COLUMN` for existing
  ones). Legacy rows keep `NULL` and **derive sign bits on the fly** from the
  stored embedding — the path degrades gracefully, never erroring.
- `search()` calls `_binary_prefilter_rows()`: a no-op (full cosine scan) when
  disabled **or** the post-filter corpus is below `min_corpus_size`; otherwise
  it keeps the `top_k * candidate_multiplier` lowest-Hamming rows and re-ranks
  them with cosine. Ties / dim-mismatched rows are force-kept so recall is
  never silently reduced below the candidate budget.

## Configuration (`args/rag_config.yaml`)

```yaml
rag:
  quantization:
    binary_prefilter:
      enabled: false          # DEFAULT OFF
      candidate_multiplier: 4  # candidates = final_top_k * this
      min_corpus_size: 512     # below this, brute-force cosine wins
```

## Recall / speed trade-off (measured)

Benchmark: 5000 × 768-dim, top_k=10, 30 queries.

**Structured / clustered embeddings** (models real sentence embeddings):

| candidate_multiplier | recall@10 (mean / min) | notes |
|---|---|---|
| 4 | 1.000 / 1.000 | meets the recall@10 ≥ 0.95 ship bar |
| 8 | 1.000 / 1.000 | |
| 16 | 1.000 / 1.000 | |

**Random Gaussian vectors** (pathological worst case — no cluster structure, so
sign bits barely correlate with cosine):

| candidate_multiplier | recall@10 (mean) | p50 latency | speedup |
|---|---|---|---|
| 4 | 0.26 | 61 ms | 5.0x |
| 16 | 0.54 | 55 ms | 5.6x |
| 64 | 0.85 | 82 ms | 3.8x |

**Takeaway:** on structured embeddings (the real workload) a small multiplier
already achieves recall@10 = 1.0; on unstructured random data binary
quantization is a weak cosine proxy and recall drops. Because recall is
corpus-dependent, the feature ships **default-OFF** and operators MUST validate
`recall@10 ≥ 0.95` on their own corpus (e.g. the rce-eval-01 harness) before
enabling. The latency win grows with corpus size, which is why it is gated
behind `min_corpus_size`.

## Back-compat

- `sign_bits` is nullable and additive — existing DBs are ALTERed in place, no
  re-index required; legacy NULL rows work via on-the-fly derivation.
- With the pre-filter disabled (default) the store behaves exactly as before,
  and the existing vector-store test suite stays green.

## Files

- `tools/rag/sqlite_vector_store.py` (+ `icdev/` mirror) — sign bits, Hamming,
  `sign_bits` column, `_binary_prefilter_rows`, config resolver.
- `args/rag_config.yaml` — `rag.quantization.binary_prefilter`.
- `tests/test_rag_quantization.py` — helpers, row-selection reduction/ordering,
  legacy NULL fallback, enabled/disabled top-k parity, default-off.
