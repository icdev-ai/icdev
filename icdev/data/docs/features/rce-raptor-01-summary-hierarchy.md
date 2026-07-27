# RCE raptor-01 — RAPTOR Summary Hierarchy above rag_chunks

CUI // SP-CTI

## Summary

RCE (RAG Capability Evolution) grows a RAPTOR-style tree of *summaries* on top of
the existing flat `rag_chunks` retrieval leaves, rather than replacing them:

```
level 0  →  leaf chunks          (live in rag_chunks — NOT stored in the new table)
level 1  →  parent summaries      (each summarizes a group of sibling leaves)
level 2  →  root / doc summary    (summarizes the level-1 summaries)
```

This card (rce-raptor-01) ships the **table + builder**. Multi-level retrieval /
dedup is the follow-on card rce-raptor-02.

## What shipped

- **Table `rag_chunk_summaries`** — co-located with `rag_chunks` in the
  vector-store DB (`data/rag/rag_vectors.db` for SQLite; the main DB for PG),
  created via `CREATE TABLE IF NOT EXISTS` in `SummaryStore._init_schema()`
  (mirroring how `SQLiteVectorStore` creates `rag_chunks`). Columns: `id`,
  `content`, `content_hash`, `embedding` (BLOB/BYTEA float32), `level`,
  `parent_chunk_id`, `child_ids` (JSON), `source_type`, `source_id`,
  `source_table`, `metadata`, `tenant_id`, `project_id`, `classification`,
  `created_at`. Carries `tenant_id` + `classification` for RLS parity.
  Co-location avoids touching `conftest.py`'s `MINIMAL_ICDEV_SCHEMA`.
- **`tools/rag/raptor.py`** — `SummaryStore` (persistence + brute-force cosine
  search over the small summary tier, pure Python / air-gap safe) and
  `RaptorBuilder`:
  - groups sibling leaves per source document into fixed-size groups
    (`group_size`), LLM-summarizes each group into a level-1 parent, embeds via
    the provider abstraction (`tools.llm.get_embedding_provider`), then a
    level-2 root summary over the level-1 summaries;
  - **idempotent per source** (clears existing summaries before rebuild);
  - the summarizer and embedder are **injectable** (tests drive structure
    without a live model) and the default summarizer uses the cheap-LLM router
    pattern (`LLMRouter.invoke(...)`, mirroring `evaluator._llm_judge`);
  - **graceful no-op**: when the LLM is unavailable the summarizer returns `""`
    and nothing is written;
  - `build_hierarchy(source_id=None, dry_run=False)` API + `--build` CLI with
    `--dry-run`.
- **Config** `rag.raptor` in `args/rag_config.yaml` — `enabled: false` (DEFAULT),
  `group_size`, `max_levels`, `summary_max_tokens`, `llm_function`,
  `summary_top_k`.

## TRUST

Summaries are LLM-generated. Each row records `metadata.provenance =
"llm_summary"` and `SummaryStore.search` tags every result
`metadata.is_summary = True`. Summaries MUST NOT be surfaced as a citation
source — citations resolve to leaf chunks (enforced in the raptor-02 retriever
merge/dedup, which prefers leaves).

## CLI

```bash
python tools/rag/raptor.py --build --json               # build all documents
python tools/rag/raptor.py --build --source <id> --json # one document
python tools/rag/raptor.py --build --dry-run --json     # plan only, write nothing
```

## Tests

`tests/test_rag_raptor.py` (fixture/temp-DB driven, no live LLM):
table creation; level-1/level-2 counts; parent/child edges
(`parent_chunk_id`/`child_ids`, root has no parent, level-1 children are leaf
ids); idempotent rebuild; summaries searchable + tagged; graceful no-op when the
LLM returns `""`; dry-run writes nothing.
