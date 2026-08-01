# RAG vector store (`data/rag/`)

## `rag_vectors.db` is a small representative SAMPLE — not the full corpus

The committed `rag_vectors.db` is a **small representative sample** of the RAG
vector store (~31 chunks in the `rag_chunks` table, one handful per
`source_type`), kept intentionally lightweight so the public repo ships a valid
default store without carrying the multi-megabyte scraped corpus.

Several tools default to `data/rag/rag_vectors.db` (e.g.
`tools/rag/sqlite_vector_store.py`, `tools/rag/raptor.py`,
`tools/rag/crag_evaluator.py`, `tools/finetune/pair_generator.py`), so a valid
file must exist here — hence the sample rather than an empty or ignored file.

### What the sample contains

- Exact `rag_chunks` schema and indexes as the full store.
- Real rows (content + real embedding blobs, 768-dim) sampled across every
  `source_type`: `creative_feature_gaps`, `creative_pain_points`,
  `creative_specs`, `innovation_signals`, `research_challenges`,
  `research_dossiers`.
- The content is public, web-scraped Creative / Research / Innovation engine
  material (public forum / review-site / signal data) and is **not sensitive**.

### The full runtime store is generated, not committed

The full vector store is produced by the RAG **ingestion pipeline** (e.g.
`mcp__icdev-unified__rag_ingest` / `tools/rag/*` ingestion, or
`tools/showcase/*` scrape + embed). Regenerate it locally to work against the
full corpus; the sample is sufficient for tests, smoke checks, and shipping a
working default.

### Baseline note

`rce_baseline.json` retains its historical RCE baseline metrics, which were
computed against the **full 1397-chunk corpus** (research/innovation-heavy, a
deliberate low-water-mark for compliance recall). Those numbers are kept as-is
for continuity; see `corpus_note` in that file.
