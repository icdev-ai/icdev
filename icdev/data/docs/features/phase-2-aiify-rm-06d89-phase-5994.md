# Phase 2 — AI-ify determination: aiify-rm-06d89-phase-5994

**Disposition:** Closed as **duplicate of aiify-opp-6086** (`_ai_metadata_extraction`).

## Opportunity
- **Kanban ID:** `aiify-rm-06d89-phase-5994`
- **Opportunity ID:** 5994
- **Roadmap:** `rm-06d89040cf` (scan_id 43)
- **Pattern → paradigm:** `metadata_extraction` → `llm_generation`
- **External module_path:** `…/aiify_git_zwu66zfu/src/documents/bulk_edit.py` (paperless-ngx)
- **Model recommendation:** claude-sonnet-4-6

## Why duplicate
The `module_path` points at a shallow-clone of the external paperless-ngx repo that the
AI-ify engine clones, scans, then deletes (`engine.py` `tempfile.mkdtemp(prefix="aiify_git_")`
→ `shutil.rmtree`). The clone `aiify_git_zwu66zfu` is already reaped — the file is gone and was
never part of this repo, so it is unmodifiable.

Per the established disposition for paperless `src/documents/*` `metadata_extraction`→
`llm_generation` opportunities, the AI-ification lands in the analogous ICDEV subsystem — the
**Document Intelligence Canvas (DIC)** ingest path — as `_ai_metadata_extraction` in
`tools/document_intelligence/ingest_orchestrator.py`. This was shipped for the canonical
sibling **aiify-opp-6086** (commit `6a388264e`) and is the same opportunity re-emitted for a
new filename (`bulk_edit.py` vs `views.py`/`workflows/mutations.py`). Filename is irrelevant —
pattern + paradigm decide the analog.

## Verification at HEAD (irad/feature, HEAD `cd21b8dc3`)
- `_ai_metadata_extraction` present: def L646, wired L1041-1043 (`extract_metadata and text.strip()`)
- `extract_metadata` flag default True: L953
- Closed-enum doc types `_METADATA_DOC_TYPES`: L614; confidence gate `_METADATA_MIN_CONFIDENCE = 0.70`: L625, enforced L712
- Result surfaced as a **HITL proposal** on `IngestOutcome.metadata` (never silently persisted)
- Tests: `tests/test_dic_ingest_metadata.py` — **17/17 pass**

Note: the original 6086 commit `6a388264e` is no longer a *direct* ancestor of HEAD (squashed /
merged via PR), but the implementation and tests are present and green at HEAD (same situation
recorded for sibling 6092).

## Action
No new code. Determination recorded here; card moved to `done` with `bypass_verification:true`
and a `bypass_reason` naming this dup determination.
