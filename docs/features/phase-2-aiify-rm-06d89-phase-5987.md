# Phase 2 — AI-ify `aiify-rm-06d89-phase-5987` (Determination: Dup of aiify-opp-6118)

**Status:** Closed as duplicate — no new implementation required.
**Date:** 2026-06-05
**Roadmap:** `rm-06d89040cf` · **Scan:** 43 · **Opportunity:** 5987

## Opportunity

- **Pattern:** `ocr_extraction_pipeline` → **AI paradigm:** `llm_generation`
- **Module (external):** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\barcodes.py`
- **Model recommendation:** `claude-sonnet-4-6`
- **Composite score:** 0.6785 (value 0.7505 / feasibility 0.845 / risk 0.775)

## Determination

The `module_path` points at a **temp `aiify_git_*` shallow clone** of the external
paperless-ngx repo (`src/documents/barcodes.py`). The AI-ify engine clones, scans, then
deletes the clone, so the file is gone and unmodifiable by the time this card runs. Per
established disposition, the AI-ification is landed in the **analogous ICDEV internal
subsystem** — the **Document Intelligence Canvas (DIC)**. The external filename is
irrelevant (`barcodes.py` vs `validators.py`) — pattern + paradigm decide the internal
analog.

For the `ocr_extraction_pipeline → llm_generation` pattern over paperless
`src/documents/*.py`, the internal analog is **`tools/document_intelligence/ingest_orchestrator.py`**,
specifically `_ai_ocr_cleanup()` — best-effort LLM correction of noisy OCR text. It runs
only when the extraction provider is OCR-based (`extraction.provider in _OCR_PROVIDERS`),
is bounded by `_OCR_CLEANUP_MAX_CHARS`, guarded by a length-ratio (0.5–2.0×) grounding
check, gated by a `clean_ocr` flag (default `True`), and surfaced via the `ocr_cleaned`
field on `IngestOutcome`. `content_sha256` hashes the RAW OCR text (pre-cleanup) to keep
idempotency deterministic. The LLM call routes through `LLMRouter` (model selected from
`.env`, not hardcoded), satisfying the card's "use claude-sonnet-4-6" intent without
violating the no-hardcoded-model rule.

This was already shipped as **aiify-opp-6118** (commit `026fe26d4`,
"add LLM OCR cleanup to DIC ingestion"). The paperless `ocr_extraction_pipeline` opp
**re-emits every scan** (opps 3765/4190/4612/4775/4974/5157/6118 across scans 17–43, plus
the `-d2`/`-d3`/`-d4` dup cards). This card (`-5987`, external `src/documents/barcodes.py`,
same pattern/paradigm) is the same AI-ification over the same paperless `src/documents/*`
subtree → **dup of 6118**.

## Verification (at HEAD, branch `irad/feature`)

- `026fe26d4` (aiify-opp-6118) confirmed **ancestor of HEAD** (`0efdb5d4c`).
- `_ai_ocr_cleanup()` present (`ingest_orchestrator.py:528`), wired into the ingest path
  (`:824`–`830`, `:1025`); `IngestOutcome.ocr_cleaned` (`:310`); `clean_ocr` flag
  default `True` (`:765`).
- `tests/test_dic_ingest_ocr_cleanup.py` — **12/12 pass**.

No code change required. Card moved to `done` with `bypass_verification: true`.
