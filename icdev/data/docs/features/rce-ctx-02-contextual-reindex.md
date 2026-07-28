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
- Tests `tests/test_rag_reindex_contextual.py` (20, fixture-driven): grouping,
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

## Windowed / resumable runs (`--limit` / `--offset`, rce-eval-04)

A full re-index re-embeds every chunk, so on a large corpus it is long enough to
be worth splitting across runs. `--limit N` bounds a run to N chunks and
`--offset M` sets the resume point; both are applied in SQL
(`LiveChunkStore.build_chunk_query`) so only the window is ever loaded.

The window is taken from a **stably ordered** chunk set —
`ORDER BY source_type, source_id, chunk_index, id`. Without that order
PostgreSQL may return rows in any order and successive windows could overlap or
skip chunks; with it, windows are disjoint and contiguous. The ordering also
keeps a document's chunks adjacent, so a window cuts at most one document.

Every windowed response carries a resume cursor:

```json
{ "total_chunks": 10, "limit": 10, "offset": 0, "next_offset": 10, "has_more": true }
```

`has_more` is a full-window heuristic (no extra `COUNT(*)`): keep advancing
`--offset` to `next_offset` until a short window comes back.

```bash
# one window at a time (dry-run first, then --execute)
python tools/rag/reindex_contextual.py --reindex --source compliance_reference \
    --limit 500 --offset 0 --dry-run --json
python tools/rag/reindex_contextual.py --reindex --source compliance_reference \
    --limit 500 --offset 0 --execute --json
```

**Caveat (documented, not fixed):** document text is reconstructed from the
chunks the run loaded, so the single document a window may cut is contextualized
against its partial text. Use windows that are large relative to a single
document when that matters.

Validated against the live `compliance_reference` corpus (3552 chunks): windows
at offsets 0/10 are disjoint and their concatenation equals `--limit 20`,
repeated runs return identical IDs, `--offset 3550` returns the 2-chunk tail,
past-the-end returns 0, and the unwindowed read still returns all 3552.
`--limit 0` / negative `--offset` are rejected by argparse (exit 2).
