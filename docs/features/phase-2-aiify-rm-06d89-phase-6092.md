# Phase 2 — aiify-rm-06d89-phase-6092 (metadata_extraction → llm_generation)

**Disposition: Duplicate of aiify-opp-6086 — closed, no new code.**

## Opportunity
- **Kanban id:** `aiify-rm-06d89-phase-6092`
- **Roadmap:** `rm-06d89040cf` (Phase 2 — Core Modernization), scan_id 43
- **Opportunity id:** 6092
- **Pattern / paradigm:** `metadata_extraction` → `llm_generation`
- **Module path:** `…/aiify_git_zwu66zfu/src/documents/workflows/mutations.py` (external paperless-ngx clone — shallow-cloned, scanned, then deleted by the AI-ify engine; unmodifiable by the time the card runs).

## Determination
External-repo AI-ify opportunities are landed in the analogous **ICDEV internal subsystem**, not the deleted clone. For paperless `metadata_extraction` → `llm_generation`, the analog is the **Document Intelligence Canvas (DIC)** ingest orchestrator's `_ai_metadata_extraction`, shipped as **aiify-opp-6086**.

`src/documents/workflows/mutations.py` is just a new filename in the same paperless document subtree as 6086's `src/documents/views.py` — the filename is irrelevant; pattern + paradigm decide the internal analog.

## Verification at HEAD (irad/feature)
- `_ai_metadata_extraction` present in `tools/document_intelligence/ingest_orchestrator.py`:
  - def at L646
  - wired into the ingest path at L1041–1043
  - `extract_metadata` flag (default `True`) at L953
  - closed `_METADATA_DOC_TYPES` enum (L614) → `"other"` fallback
  - `_METADATA_MIN_CONFIDENCE = 0.70` gate (L625, enforced L712)
  - surfaced as a HITL proposal on `IngestOutcome.metadata` (never silently persisted)
- Tests: `tests/test_dic_ingest_metadata.py` — **17 passed**.

(Original 6086 commit `6a388264e` is no longer a direct ancestor of HEAD `8d195d1eb` — squashed/merged via PR — but the substantive implementation and tests are present and green at HEAD.)

## Outcome
No new code required. Card moved to **done** with `bypass_verification: true` (verification-only, test-only — no new implementation).
