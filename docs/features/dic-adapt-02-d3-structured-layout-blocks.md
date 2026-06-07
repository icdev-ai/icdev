# dic-adapt-02-d3 — Preserve structured layout blocks on ingest

[TEMPLATE: CUI // SP-CTI]

## Problem

A layout-detection backend (PaddleOCR PP-Structure / DocLayout-YOLO; see
`tools/document_intelligence/extractors.py`, dic-adapt-02-d1) yields a document
as a list of typed regions — *blocks* — instead of one flat text blob: tables
(with cell/row structure), figures (with a caption + bounding box), and ordinary
text/title regions. The ingest orchestrator previously consumed only the flat
`Extraction.text`, so table grids were concatenated into prose and a figure's
caption was divorced from its position. Downstream RAG/search could no longer
reason over rows/columns or surface figure metadata.

## Change

Scoped to `tools/document_intelligence/ingest_orchestrator.py`:

1. **Accept blocks.** `Extraction` now carries a `blocks: list[dict]` field, and
   `_select_extractor` / `_try_provider_package` preserve the extractor's
   `metadata` and lift any `blocks` / `layout_blocks` payload onto it.
   `ingest_file(...)` gains an explicit `blocks=` argument (takes precedence over
   extractor-supplied blocks).
2. **Preserve structure → `dic_sections`.** New deterministic, offline helpers
   map each block into a structured section row:
   - **table** → `content` is a JSON grid of cells/rows (`{"rows": [...],
     "n_rows", "n_cols"}`), *not* concatenated text; `block_json` holds the same
     grid plus page/bbox geometry. Supports both `rows` (list-of-lists) and flat
     `cells` (reassembled by row/col indices) shapes.
   - **figure** → caption + bounding box are stored in `block_json` **separately**
     from the main content block.
   - **other typed region** → a text section preserving reading order + geometry.
3. **Schema.** Two additive columns on `dic_sections`: `block_type`, `block_json`
   (CREATE + idempotent `ALTER TABLE` backfill). Existing column-explicit
   INSERTs (blueprint/doc_generator) are unaffected.
4. **Idempotent.** Layout sections are written with `origin='layout_extracted'`
   and replaced in place on re-ingest, leaving human/AI-authored sections intact.
   `IngestOutcome.structured_sections` reports the count.

The flat-text path (chunk → embed → KG bridge) is unchanged; block persistence
never raises into the ingest path (a bad block is skipped).

## Acceptance

When ingesting a document with table blocks, the generated `dic_sections` rows
contain structured JSON representations of cells/rows rather than concatenated
text; figure blocks keep their caption + bbox in `block_json` separate from the
content block.

## Tests

`tests/test_dic_ingest_structured_blocks.py` (8 tests):
- `_table_grid` from `rows` and from flat `cells`; `None` without structure.
- table section content is parseable JSON (not prose); figure caption + bbox in
  `block_json`; empty blocks skipped.
- end-to-end `ingest_file(..., blocks=[...])` persists table/figure/text sections
  with `block_type`/`block_json` and `origin='layout_extracted'`.
- re-ingest idempotency (content-hash dedup path leaves layout sections intact).
