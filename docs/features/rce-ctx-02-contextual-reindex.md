# CUI // SP-CTI

# rce-ctx-02 — Contextual re-index + measure vs baseline

Follow-on to **rce-ctx-01** (contextual retrieval prefixing at ingestion). This
task adds a re-index path so an **existing** corpus can adopt context prefixes
without re-ingesting from source, plus a thin convenience to measure retrieval
quality **before/after** against the saved baseline.

## What shipped

- **`tools/rag/reindex_contextual.py`**
  - `ContextualReindexer` — pure, fully injectable re-index logic:
    1. groups stored chunks by `(source_type, source_id)` and reconstructs each
       source "document" by joining its chunks in `chunk_index` order;
    2. generates a context prefix per chunk (via the rce-ctx-01
       `contextualize_chunk`), sets `embed_text`;
    3. recomputes the embedding on the **contextualized** text;
    4. persists the updated embedding + metadata via an injected `store_update`.
  - **Resumable / idempotent** — chunks already carrying a `context_prefix` in
    metadata are skipped unless `--force`.
  - **Retention-aware** — cold-tier (embedding-stripped) chunks are skipped.
  - **`LiveChunkStore`** — the live (default sqlite/pg) adapter that reads
    `rag_chunks` and issues `UPDATE rag_chunks SET embedding=?, metadata=? WHERE id=?`.
    Only touched on the guarded `--execute` path. (`upsert` can't be reused for
    re-embedding because it dedups/​skips on the unchanged `content_hash`.)
  - `run_benchmark_compare(...)` — runs the golden-set benchmark
    (`tools/rag/rag_benchmark.py`) and attaches per-metric deltas vs a baseline.
- Mirrored verbatim to `icdev/tools/`; manifest row added.
- Tests `tests/test_rag_reindex_contextual.py` (9, fixture-driven): grouping,
  dry-run plans-without-writing, live update uses contextualized embed text,
  idempotent skip, `--force`, cold-tier skip, no-provider error path, and
  benchmark-compare delta wiring — all with injected fakes, no live corpus/DB.

## Measure vs baseline — exact reproduction command

Re-embedding a full corpus needs the **live corpus + embedding provider**, which
are not present in a fresh worktree or CI, so the measured delta is **not** run
here. To reproduce on a populated environment:

```bash
# 0) (once) enable contextual retrieval
#    args/rag_config.yaml → rag.contextual_retrieval.enabled: true

# 1) capture the pre-change baseline (if not already saved)
python tools/rag/rag_benchmark.py --baseline-out data/rag/rce_baseline.json --json

# 2) live re-index the corpus with contextual prefixes
python tools/rag/reindex_contextual.py --reindex --execute --json
#    (optionally restrict: --source compliance_artifacts)

# 3) measure after vs the saved baseline
python tools/rag/reindex_contextual.py --benchmark --baseline data/rag/rce_baseline.json --json
#    equivalent to:
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
```

Step 3 prints per-metric deltas (`recall_at_k`, `mrr`, `ndcg_at_k`,
`citation_hit_rate`). The wiring was verified against the committed
`data/rag/rce_baseline.json` (the retriever returns a zeroed run without a
corpus, so all deltas compute cleanly to 0.0 rather than crashing).

## Observed delta

Not measured in this worktree (no live corpus). Per rce-ctx-01, the Anthropic
pattern claims up to ~67% retrieval-failure reduction; that figure is **not**
asserted here — run the command above on a populated corpus to record the real
delta in the benchmark JSON artifact.

> **Default-recommendation note:** if the measured gain is `<5%` recall@k on a
> real corpus, flag `rag.contextual_retrieval` as not-recommended-default in the
> `args/rag_config.yaml` comment (the toggle already ships **OFF** by default).
