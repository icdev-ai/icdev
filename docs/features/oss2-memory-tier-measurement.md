# CUI // SP-CTI

# Does the memory tier earn its keep? (oss2-meas-01)

**Card:** OSS-02 Nine-Project Adaptation (`oss2-`)
**Spike:** [docs/spikes/oss-02-nine-project-adaptation.md](../spikes/oss-02-nine-project-adaptation.md) §3.4
**Instrument:** `tools/memory/memory_tier_measure.py`
**Measured:** 2026-07-26, against the live `memory_entries` table (98 entries).

## Why this exists

The spike's sequencing argument: ICDEV already owns the architecture mem0 describes
(tiering, capture, consolidation, hybrid retrieval, decay, scheduled upkeep), but its
consolidation stage was **dead code** (`oss2-fix-04` / D5). The instruction was: fix
consolidation first (done), **then measure** whether the tier earns its keep before
building anything mem0-shaped. This is that measurement.

## Methodology — and a correction to the spike's pointer

The spike pointed at `tools/rag/rag_benchmark.py` + `args/rag/golden_query_set.yaml`.
That harness measures **RAG document retrieval** against a compliance/NIST corpus — a
*different* system from the memory tier (`tools/memory/` over `memory_entries`,
recalled into the agent loop). So the memory tier needs its own instrument, which is
what `memory_tier_measure.py` provides.

The decisive question (spike §3.3) is **consolidation impact**: exact-hash dedup
already works, so the only thing the repaired `MemoryConsolidator` adds is merging
memories that are *semantically* redundant but differ byte-for-byte. The instrument
scans `memory_entries` pairwise using the **consolidator's own** keyword/Jaccard
similarity (`_extract_keywords` / `_jaccard_similarity`), so the redundancy it counts
is exactly what consolidation would act on.

**Honest-measurement guardrail.** Learned from the oss-adaptation golden-set lesson (a
measurement over too little data launders a verdict): below `MIN_SAMPLE = 30` entries
the instrument returns `insufficient_data` and makes no keep/drop claim. The 98-entry
corpus clears that bar.

## Results (live, 98 entries)

**Baseline**
- Total entries: **98** (89 `insight`, 8 `event`, 1 `thinking`)
- Exact-hash duplicates: **0** — byte-hash dedup is working.
- Embedding coverage: **0.0%** — *none* of the 98 entries carry an embedding.

**Consolidation redundancy** (fraction of entries with ≥1 semantic near-duplicate that byte-hash dedup missed):

| Jaccard threshold | near-dup pairs | entries with a near-dup | redundancy rate | verdict |
|---|---|---|---|---|
| 0.75 | 165 | 63 / 98 | **64.3%** | earns its keep |
| 0.85 | 82 | 52 / 98 | **53.1%** | earns its keep |
| 0.90 (near-identical) | 41 | 40 / 98 | **40.8%** | earns its keep |

## Findings

1. **Consolidation earns its keep — decisively.** Even at a strict 0.90 keyword-Jaccard
   bar, **41% of entries have a semantic near-duplicate** that exact-hash dedup let
   through. This redundancy accumulated *because consolidation was dead code* — it is
   the direct cost of the D5 defect, now quantified. The `oss2-fix-04` repair was
   worthwhile, and running consolidation (`auto_consolidate` / `maintenance_cron`)
   over this corpus would materially shrink it.

2. **The "semantic" retrieval half is currently inert.** Embedding coverage is **0%**,
   so `hybrid_search`'s semantic path has nothing to rank on this corpus — retrieval is
   effectively BM25/keyword-only today. This is a separate defect worth a follow-up
   (why `embed_memory` is not populating `memory_entries.embedding`), independent of
   the mem0 question.

## Recommendation

- **Do NOT build a second, mem0-shaped memory system.** ICDEV's own tier now works
  and has clear, measured headroom (40–64% redundancy) for its *existing*
  consolidation to reduce. A second system would compete with `tools/memory/`, the
  agent-loop tier, `co_learning_store`, the KG, and `chat_memory.py` for a capability
  ICDEV already owns.
- **Run the repaired consolidation** on the live corpus and re-measure; the redundancy
  rate is the success metric.
- **Investigate the 0% embedding coverage** as a distinct item — the semantic tier
  cannot earn its keep while it holds no vectors.
- **Intent-relative temporal ranking (mem0's past/current/upcoming): defer.** The
  spike flagged it as "not present"; this measurement finds no evidence it is the
  binding constraint — redundancy is, and consolidation already addresses that. Build
  it only if a later measurement shows recency decay mis-ranking intent, per the
  spike's "build only if the numbers say so."

## Reproduce

```bash
python tools/memory/memory_tier_measure.py --json                 # default 0.75 threshold
python tools/memory/memory_tier_measure.py --threshold 0.90 --json # near-identical only
```

Runs read-only against the configured backend's `memory_entries`. Returns
`insufficient_data` (no verdict) below 30 entries.

# CUI // SP-CTI
