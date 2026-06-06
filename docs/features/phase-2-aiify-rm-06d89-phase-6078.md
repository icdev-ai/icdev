# Phase 2 — AI-ify `aiify-rm-06d89-phase-6078` (ocr_extraction_pipeline → llm_generation)

**Disposition:** Closed as **duplicate of aiify-opp-6118** (commit `026fe26d4`).

## Opportunity
- **opportunity_id:** 6078
- **scan_id:** 43
- **roadmap_id:** rm-06d89040cf
- **pattern_type:** ocr_extraction_pipeline
- **ai_paradigm:** llm_generation
- **model_recommendation:** claude-sonnet-4-6
- **module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\utils.py`

## Why this is a duplicate
The `module_path` points at a temp `aiify_git_*` shallow-clone of the external **paperless-ngx** repo, which the AI-ify engine clones, scans, then deletes (`engine.py` `shutil.rmtree`). The file is gone and was never part of ICDEV. Per the established disposition for these external-repo opps, the AI-ification is landed in the **analogous ICDEV subsystem** rather than the external file.

The paperless `ocr_extraction_pipeline → llm_generation` pattern re-emits on every scan (opps 3765/4190/4612/4775/4974/5157/6118 across scans 17–43, plus roadmap-phase re-emissions like 5987). It was already implemented as **aiify-opp-6118**:

- `tools/document_intelligence/ingest_orchestrator.py`:
  - `_ai_ocr_cleanup(text)` (L529) — best-effort LLM correction of noisy OCR text via `LLMRouter().invoke("summarization", …)` (model selected from `.env`, not hardcoded).
  - Gated to OCR providers only (`extraction.provider in _OCR_PROVIDERS`, L1022), bounded by `_OCR_CLEANUP_MAX_CHARS`, length-ratio (0.5–2.0×) grounding guard.
  - `clean_ocr` flag default `True` (L952); `IngestOutcome.ocr_cleaned` (L311, surfaced in dict L329, set L1027/L1233).
  - `content_sha256` hashes RAW OCR text (pre-cleanup) to keep idempotency deterministic.
- Tests: `tests/test_dic_ingest_ocr_cleanup.py` — **12/12 pass**.

## Verification (2026-06-05)
- `git merge-base --is-ancestor 026fe26d4 HEAD` → **026fe26d4 IS ancestor of HEAD** (irad/feature).
- `_ai_ocr_cleanup` present (def L529, wired L1022–1027, IngestOutcome.ocr_cleaned L311, clean_ocr default True L952).
- `pytest tests/test_dic_ingest_ocr_cleanup.py` → **12 passed**.

No new code required. Card moved to done with `bypass_verification:true` + `bypass_reason`.
