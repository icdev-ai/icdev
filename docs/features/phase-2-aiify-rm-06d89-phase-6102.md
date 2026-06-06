# Phase 2 — AI-ify `aiify-rm-06d89-phase-6102` (ocr_extraction_pipeline → llm_generation)

**Status:** Closed as **duplicate of aiify-opp-6118** (DIC `_ai_ocr_cleanup`).
**Date:** 2026-06-05

## Opportunity
- **Kanban id:** `aiify-rm-06d89-phase-6102`
- **Roadmap:** `rm-06d89040cf` · **scan_id:** 43 · **opportunity_id:** 6102
- **Pattern / paradigm:** `ocr_extraction_pipeline` → `llm_generation`
- **External module_path:** `…/aiify_git_zwu66zfu/src/paperless/parsers/tesseract.py`
- **Model recommendation:** `claude-sonnet-4-6`

## Determination
The `module_path` points at a temp `aiify_git_*` shallow-clone of the external
**paperless-ngx** repo, which the AI-ify engine clones, scans, then deletes. The
file is gone at run time (verified MISSING) and was never part of ICDEV.

This is the same `ocr_extraction_pipeline → llm_generation` opportunity that
re-emits every scan against the paperless OCR subtree (opps
3765/4190/4612/4775/4974/5157/6118; siblings `barcodes.py`/`validators.py`/now
`parsers/tesseract.py` — filename is irrelevant, the pattern+paradigm decide the
internal analog). The AI-ification already shipped in the analogous ICDEV
subsystem, the **Document Intelligence Canvas (DIC)**.

## Existing implementation (verified at HEAD on `irad/feature`)
- `tools/document_intelligence/ingest_orchestrator.py`
  - `_ai_ocr_cleanup(text)` — LLM corrects noisy OCR text (def L529)
  - wired only when `extraction.provider in _OCR_PROVIDERS`, gated by `clean_ocr`
    flag (default True, L952) with a length-ratio grounding guard (L1022-1027)
  - `IngestOutcome.ocr_cleaned` flag (L311, serialized L329)
  - LLM routed via `LLMRouter` (model selected from `.env`, **not** hardcoded —
    satisfies the card's "claude-sonnet-4-6" ask without violating the
    CLAUDE.md no-hardcoded-model-ID rule)
- Tests: `tests/test_dic_ingest_ocr_cleanup.py` — **12/12 pass**
- Commit: `026fe26d4` (ancestor of HEAD), merged context per
  `aiify-external-repo-opps-land-in-dic`.

## Action
No code change required. Card moved to **done** with
`bypass_verification: true` + `bypass_reason` naming this determination.
