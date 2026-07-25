# Phase RCE — RAG Context Engineering

**Classification:** CUI // SP-CTI
**Card:** RCE (RAG Context Engineering)
**Status:** Shipped (rce-gate-00 held for manual release)
**Source analysis:** `C:\AI\searches\archive\rag_alt.md`

## Thesis

The ICDEV™ RAG stack is already advanced (pgvector HNSW + tsvector hybrid RRF,
two-stage BGE/qwen3 reranking, GraphRAG, citation/provenance, dual PG↔SQLite
backends). The next gains come from **evolving the existing pipeline** —
better chunk context, a summary hierarchy, and cheaper vectors — **not** from
swapping backends. This card is "context engineering," per Jeff Huber's framing:
retrieval is largely solved; the leverage is in *what goes in the context box*.

Every change is **measured** against a committed baseline (rce-eval-01), is
**opt-in / default-OFF**, is **pure-Python and air-gap safe**, and preserves the
TRUST invariants (citations resolve to original leaf chunks; provenance
persisted).

## What shipped

| Task | PR | What |
|------|----|------|
| rce-eval-01 | #658 | Retrieval-quality baseline harness — golden query set + `rag_benchmark.py` (recall@k, MRR, citation-hit-rate, ndcg; reuses `evaluator.mrr`/`ndcg_at_k`), committed `data/rag/rce_baseline.json`. |
| rce-ctx-01 | #664 | Contextual retrieval — ~50-100 token LLM context prefix per chunk at ingestion; embed contextualized text, cite/store original; `VectorChunk.text_for_embedding()`. |
| rce-ctx-02 | #670 | Contextual re-index CLI (`reindex_contextual.py`) + measure-vs-baseline wiring. |
| rce-quant-01 | #666 | float16 vector packing (self-describing `RVQ1` header, float32 back-compat, fixes latent headerless-float16 `migrate_tier` bug). Default `sqlite_dtype: float16` → ~48% DB shrink. |
| rce-quant-02 | #674 | Optional binary quantization + Hamming pre-filter (`sign_bits` column), default OFF, cosine re-rank of candidates. |
| rce-raptor-01 | #675 | RAPTOR summary hierarchy — `rag_chunk_summaries` table + `raptor.py` tree builder (cheap-LLM summaries, graceful no-op). |
| rce-raptor-02 | #679 | Multi-level retrieval + lineage dedup in `RAGRetriever.search`. |
| rce-eval-02 | #663 | SPIKE — domain-adapted embedding feasibility: **NO-GO / DEFER** + `embedding_feasibility.py` re-runnable probe. |
| rce-xcut-01 | (this) | Cross-cutting docs, manifest, skip-decision ADRs, coherence/companion sync. |

## Toggles (all default OFF except the float16 storage win)

`args/rag_config.yaml`:

```yaml
rag:
  contextual_retrieval:
    enabled: false        # rce-ctx: prepend LLM context prefix before embedding
  quantization:
    sqlite_dtype: float16 # rce-quant-01: 16-bit SQLite vectors (float32 back-compat)
    binary_prefilter:
      enabled: false      # rce-quant-02: Hamming pre-filter then cosine re-rank
  raptor:
    enabled: false        # rce-raptor: search summary tiers + dedup with leaves
```

- **contextual_retrieval** and **raptor** need an LLM at ingestion/build time;
  both no-op gracefully when the provider is unavailable (air-gap).
- **sqlite_dtype: float16** is the one behavior change that ships ON — reads are
  back-compat for legacy float32 and headered float16, so no re-index is forced.
- **binary_prefilter** is OFF pending per-corpus recall validation (random
  Gaussian embeddings are its documented worst case).

## Benchmark deltas

Baseline (`data/rag/rce_baseline.json`), generated through the full retriever
against the live 1397-chunk corpus: **recall@5 = 0.12, MRR = 0.20,
citation_hit_rate = 0.24**.

The absolute numbers are a deliberate **low-water-mark**: the live corpus is
research/innovation-heavy and holds ~0 NIST/compliance chunks, while the golden
set targets the compliance product. Per-change retrieval deltas require a
populated compliance corpus + LLM and are **not** reproducible in a fresh
worktree/CI (empty `rag_chunks`). Each change therefore ships with the exact
reproduction command:

```bash
# Ingest compliance corpus, then for a given change:
python tools/rag/reindex_contextual.py --reindex --execute   # or: python tools/rag/raptor.py --build
# enable the toggle in args/rag_config.yaml, then:
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
```

Storage delta (rce-quant-01, measured): 768-dim × 2000 vectors → SQLite DB
**8.50 MB → 4.39 MB (−48%)**, query latency within noise.

## Evaluated and SKIPPED (see ADRs D-RCE-*)

- **TurboQuant** — no pgvector integration; HNSW already covers ANN. Skip.
- **Turbopuffer** — cloud-only, no local/embedded mode; breaks the SQLite
  air-gap fallback. Skip.
- **Qdrant** — has a local mode but adds a Rust dependency, breaking the
  pure-Python + SQL fallback philosophy. Deferred (revisit only if the SQLite
  fallback becomes a measured bottleneck).
- **Domain-adapted embedding fine-tune** — NO-GO / DEFER (rce-eval-02): no
  in-domain training data yet; re-evaluate after compliance-corpus ingestion.

## Guardrails honored

Pure-Python / air-gap safe (no new heavy deps, no npm). New behavior default-OFF.
`rag_chunk_summaries` carries `tenant_id` + `classification` (RLS parity) and is
**not** append-only. `sign_bits` is a nullable, back-compat column. All `tools/`
changes mirrored to `icdev/tools/`. New tools registered in
`tools/manifest/rag-subsystem.md`.
