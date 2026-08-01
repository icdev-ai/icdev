# RCE raptor-02 — Multi-level RAPTOR retrieval + dedup

CUI // SP-CTI

## Summary

Follow-on to `rce-raptor-01` (which shipped the `rag_chunk_summaries` table +
builder). This card teaches `RAGRetriever.search` to retrieve from **both** the
flat `rag_chunks` leaves **and** the RAPTOR summary tiers, merge them, and
**deduplicate by lineage** — preferring leaves for citation while letting
summaries rescue weak leaf retrieval.

Behavior is gated behind `rag.raptor.enabled` (**default OFF**). When disabled
the pipeline is byte-for-byte the pre-existing flat-leaf path.

## What shipped (`tools/rag/retriever.py`)

- `RAGRetriever.__init__` loads `self._raptor_cfg = rag.raptor`.
- `RAGRetriever._search_summaries(query_embedding, top_k, project_id)` — searches
  the summary tier via `raptor.SummaryStore.search` (768-dim query embedding
  reused from the leaf search). **Best-effort**: returns `[]` when the summary
  store / table is absent (e.g. no hierarchy built yet), so enabling raptor
  never breaks retrieval.
- `_merge_raptor_results(leaf_results, summary_results)` — merges the summary
  hits into the leaf candidate pool **before** RRF fusion / re-rank, then dedups
  by lineage:
  - summaries are processed **lowest-level-first**;
  - a summary is **dropped** when any of its `child_ids` is already represented
    (a leaf, or a lower-level summary that survived) — i.e. the finer-grained
    result wins;
  - summaries whose children are absent **survive as fallback context**
    (weak-leaf-retrieval rescue).
- Wired into `search()` immediately after the leaf vector search, behind the
  `if self._raptor_cfg.get("enabled", False):` gate — so fusion, time-decay and
  re-rank treat summaries as ordinary candidates, and the disabled path is
  unchanged.

The existing hybrid RRF fusion is **reused** (summaries flow through the same
`_rrf_fusion`); no second fusion path was added (Karpathy: three clear lines
over one clever abstraction).

## TRUST — citations resolve to leaves

Summary `SearchResult`s carry `metadata.is_summary = True` (set by
`SummaryStore.search`) and are preferred-against during dedup. They provide
*assembly context* only and must never be surfaced as a citation source;
citations resolve to leaf chunks.

## Backends

Works on SQLite (`data/rag/rag_vectors.db`) and PostgreSQL — `SummaryStore` uses
`%s` placeholders throughout (translated for SQLite, native for PG) and computes
cosine in Python over the small summary tier (no pgvector dependency).

## Measure vs baseline

The `rce-eval-01` harness scores a compliance/NIST golden set (recall@k, MRR,
nDCG@k, citation-hit-rate) against a committed baseline:

```bash
# Baseline (raptor OFF) is committed at data/rag/rce_baseline.json.
# 1) Populate the corpus and build the summary hierarchy:
python tools/rag/ingestion_manager.py --sweep          # ingest leaves
python tools/rag/raptor.py --build --json              # build summary tiers

# 2) Enable raptor, then compare against the OFF baseline:
#    (set rag.raptor.enabled: true in args/rag_config.yaml)
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
```

**Observed delta in this build environment: not measurable here.** The CI /
fresh-worktree checkout ships an **empty** vector store (`no such table:
rag_chunks`), so the golden queries score 0 and no live delta can be produced
without a populated corpus. The command above is the exact reproduction on an
environment with an ingested corpus; multi-hop / summary-style golden queries
are where the summary tier is expected to lift recall (leaf retrieval that
misses spread-out evidence is rescued by a level-1/level-2 summary).

## Tests (`tests/test_rag_raptor.py`, raptor-02 section)

Fixture-driven, no live corpus:
- `_merge_raptor_results` unit: empty-identity; parent dropped when child leaf
  present; summary kept when children absent; level-1 preferred over level-2
  parent; summaries stay tagged `is_summary`.
- Retriever integration (mocked embedding provider + vector store):
  **disabled = old path** (summary tier never consulted); enabled merges a
  fallback summary; enabled dedups a parent whose child leaf is present; a
  missing summary table is safe (best-effort `[]`).
