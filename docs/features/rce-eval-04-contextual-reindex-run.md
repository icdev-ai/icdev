# rce-eval-04-d3 — compliance_reference contextual re-index (run record)

**Classification:** CUI // SP-CTI
**Date:** 2026-07-25
**Task:** `rce-eval-04-d3` — Reindex `compliance_reference` with contextual retrieval
**Command:** `python tools/rag/reindex_contextual.py --reindex --source compliance_reference --execute`

## Outcome

The full live re-index completed. Every chunk in the corpus now carries a
contextual prefix and a matching pgvector embedding.

| Metric | Value |
|--------|-------|
| Chunks in `compliance_reference` | 3552 |
| Chunks with `metadata.context_prefix` | 3552 (100%) |
| Chunks with `embedding_vec` populated | 3552 (100%) |
| Source documents spanned | 2005 |
| Errors | 0 |
| Wall clock (initial pass) | ~125 min (~1.8 s/chunk) |

Context-generation method split, as persisted in `metadata.context_provenance`:

| Method | Chunks |
|--------|--------|
| `llm` (`rag_evaluate` router function) | 3148 |
| `heuristic` (`source_type+metadata` fallback) | 404 |

The initial pass reported 4111 processed chunk-visits against a 3552-row corpus;
the re-run below is the authoritative per-row count.

## Verification

Re-running the same command is a no-op, which is the idempotency contract in
`ContextualReindexer.reindex` (already-contextualized chunks are skipped unless
`--force`):

```json
{
  "total_chunks": 3552,
  "documents": 2005,
  "planned": 0,
  "reindexed": 0,
  "skipped_existing": 3552,
  "skipped_cold": 0,
  "skipped_no_prefix": 0,
  "errors": [],
  "has_more": false
}
```

Direct PG read-back over `rag_chunks WHERE source_type = 'compliance_reference'`
confirms `context_prefix` and `embedding_vec` are both non-null on all 3552 rows.

## Prerequisite fix folded into this task

The first full pass wrote contextual embeddings that search could not see.
`LiveChunkStore.update_chunk` persisted only the legacy `embedding` BYTEA blob,
while `pg_vector_store` ranks on the pgvector `embedding_vec` column
(`ORDER BY embedding_vec <=> %s::vector`). Every chunk therefore kept its
pre-re-index vector — a silent no-op that would have shown a retrieval delta of
exactly zero rather than an error. Two corrections landed alongside the run:

* `update_chunk` writes `embedding_vec` alongside the blob on PostgreSQL, behind
  an explicit `is_pg` branch, with the SQLite path unchanged.
* `iter_chunks` decodes `memoryview` as well as `bytes` — psycopg returns BYTEA
  as a memoryview, which `struct.pack` rejected, so the live PG read path had
  been yielding opaque buffers.

`rag.contextual_retrieval.enabled` is also flipped to `true`, the documented
close-out step in
[phase-rce-rag-context-engineering.md](phase-rce-rag-context-engineering.md)
("run the reindex, then set `enabled: true`, then benchmark").

## Operational notes

* The run needs a live PostgreSQL backend and `.env` from the shared checkout —
  worktrees do not carry `.env`. Clear `GITHUB_TOKEN` before invoking.
* `--limit` / `--offset` make the pass resumable; `next_offset` / `has_more` in
  the JSON envelope drive the next slice. A ~2 h run does not have to be
  restarted from zero.
* `--dry-run` plans without writing. Note that unlike the RAPTOR builder, this
  CLI's dry run does **not** issue the LLM calls.

## Follow-on

`rce-eval-04-d4` can now benchmark contextual retrieval against the
`data/rag/rce_baseline_compliance.json` baseline — the corpus is indexed and the
toggle is on, so a measured delta is finally meaningful.

## Related

* [rce-ctx-02-contextual-reindex.md](rce-ctx-02-contextual-reindex.md) — the re-index tool
* [rce-eval-01-retrieval-baseline.md](rce-eval-01-retrieval-baseline.md) — baseline harness
* [phase-rce-rag-context-engineering.md](phase-rce-rag-context-engineering.md) — phase overview
