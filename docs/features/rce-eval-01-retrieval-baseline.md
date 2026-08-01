# RCE — Retrieval-Quality Baseline Harness (rce-eval-01)

**Classification:** CUI // SP-CTI
**Card:** RCE (RAG Context Engineering)
**Status:** Shipped

## Purpose

The first RCE task. Establishes a **repeatable retrieval-quality baseline** so
every later RCE change — contextual retrieval prefixes (rce-ctx), RAPTOR summary
hierarchy (rce-raptor), SQLite vector quantization (rce-quant) — is *measured
against a fixed ground truth*, not asserted.

## Components

| Artifact | Path | Role |
|----------|------|------|
| Golden query set | `args/rag/golden_query_set.yaml` | ~33 compliance/NIST queries with expected hits |
| Benchmark CLI | `tools/rag/rag_benchmark.py` | Scores the golden set against the current retriever |
| Baseline artifact | `data/rag/rce_baseline.json` | Committed reference run for delta comparison |
| Tests | `tests/test_rag_benchmark.py` | Fixture-driven, no live corpus/DB |

## Metrics

All averaged over queries that declare at least one expected target:

- **recall@k** — fraction of a query's expected targets found in the top-k results.
- **MRR** — mean reciprocal rank of the first matching result (reuses
  `tools/rag/evaluator.py::mrr` — scoring is **not** re-implemented).
- **citation_hit_rate** — fraction of queries with ≥1 expected target in top-k.
- **ndcg@k** — ranking quality of matched results (reuses `evaluator.ndcg_at_k`).

## Re-index-safe ground truth

Expected hits are expressed primarily as **content substrings** (optionally exact
`chunk_ids` / `source_ids`). Re-ingestion reassigns chunk IDs, so substring
targets keep a before/after run comparable across the contextual-retrieval and
RAPTOR changes that re-index the corpus.

## Baseline results

Generated through the full `RAGRetriever` pipeline (vector → RRF hybrid → rerank)
against the live corpus (`data/rag/rag_vectors.db`, 1397 chunks) with embeddings
via the provider abstraction (`get_embedding_provider()`):

| Metric | Value |
|--------|-------|
| recall@5 | 0.12 |
| MRR | 0.20 |
| ndcg@5 | 0.20 |
| citation_hit_rate | 0.24 |

The current corpus is research/innovation-heavy and contains little
NIST/compliance content, so recall against the compliance golden set is a
deliberate **low-water-mark**. Later RCE work and compliance ingestion are
expected to raise it. The point of the artifact is the *delta*, not the absolute.

## Usage

```bash
# Score the current retriever against the shipped golden set
python tools/rag/rag_benchmark.py --json

# Regenerate the committed baseline artifact
python tools/rag/rag_benchmark.py --baseline-out data/rag/rce_baseline.json --json

# After an RCE change: measure the delta vs the committed baseline
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
```

`--compare` emits a per-metric `{baseline, current, delta}` block — the contract
that rce-ctx-02 and rce-raptor-02 use to prove their change moved the needle.

## Design notes

- **PG-primary; SQLite-fallback safe.** The benchmark drives the existing
  retriever, which selects its backend via the vector-store factory. No new
  backend logic.
- **Air-gap safe.** Pure Python + PyYAML (already a dependency). No new packages.
- **Deterministic tests.** The harness accepts an injected `search_fn` /
  `retriever`, so `tests/test_rag_benchmark.py` runs without a corpus or DB —
  green in a fresh worktree.
