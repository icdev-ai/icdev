# Phase 2 — aiify-rm-06d89-phase-6093 (determination: duplicate of 6086)

**Opportunity:** 6093 · **Scan:** 43 · **Roadmap:** rm-06d89040cf
**Pattern:** `metadata_extraction` → `llm_generation`
**External module:** `…/aiify_git_zwu66zfu/src/documents/workflows/mutations.py` (paperless-ngx clone)

## Determination

This is an **external-repo opportunity**. The `module_path` points at a temporary
`aiify_git_*` shallow-clone of paperless-ngx that the AI-ify engine scans and then
deletes — the file is unmodifiable by the time the card runs and is not part of
ICDEV. Per the established disposition for paperless `src/documents/*`
`metadata_extraction`→`llm_generation` opportunities, the AI-ification was landed
in the analogous **ICDEV internal subsystem** — the Document Intelligence Canvas
(DIC), `tools/document_intelligence/ingest_orchestrator.py`.

6093 is an **exact sibling** of the already-closed `aiify-rm-06d89-phase-6092`
(same external file `src/documents/workflows/mutations.py`, same pattern/paradigm),
itself a duplicate of opp **6086** which shipped the canonical implementation.

## Existing implementation (opp 6086)

`_ai_metadata_extraction(text, filename)` in
`tools/document_intelligence/ingest_orchestrator.py`:

- LLM proposes structured `document_type` (closed enum `_METADATA_DOC_TYPES`,
  defaults to `"other"`), `tags` (lower-cased / de-duped / length+count capped),
  and a real ISO `date`.
- Gated by `_METADATA_MIN_CONFIDENCE = 0.70`; below-threshold proposals are
  discarded (HITL / manual fallback).
- Surfaced as a **HITL proposal** on `IngestOutcome.metadata` — never silently
  persisted.
- Controlled by the `extract_metadata` flag (default `True`).
- Model selected via the LLM Router from `.env` (no hardcoded model ID).

Verified present at HEAD `191da9b94` (irad/feature):
- def `_ai_metadata_extraction` L646
- `_METADATA_DOC_TYPES` L614, `_METADATA_MIN_CONFIDENCE = 0.70` L625/L712
- wired L1041–1043, `extract_metadata: bool = True` L953

## Verification

- `tests/test_dic_ingest_metadata.py` — **17/17 pass** at HEAD `191da9b94`.

## Disposition

Closed as **duplicate of 6086**. Card moved to `done` with
`bypass_verification: true` (no competing copy authored; no kanban_verifications
row for a dup).
