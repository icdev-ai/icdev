<!-- CUI // SP-CTI -->
# aiify-rm-06d89-phase-6110 — Determination: dup of aiify-opp-6118

- **Roadmap:** rm-06d89040cf
- **Scan:** 43
- **Opportunity:** 6110
- **Pattern:** `ocr_extraction_pipeline` → `llm_generation`
- **External module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\paperless\parsers\tesseract.py`
- **Model recommendation:** claude-sonnet-4-6

## Determination

This roadmap-phase card is an **exact sibling of aiify-opp-6118 / 6102 / 6107 / 6108 / 6109**:
same `ocr_extraction_pipeline` → `llm_generation` pattern over the external **paperless-ngx**
OCR subtree (`src/paperless/parsers/tesseract.py`). The `module_path` points at a temporary
`aiify_git_zwu66zfu` shallow-clone that the AI-ify engine creates, scans, and deletes — the
file is external and unmodifiable by the time this card runs.

Per the established disposition for external-repo AI-ify opps, the OCR `llm_generation`
modernization was landed in the **analogous ICDEV internal subsystem** — the Document
Intelligence Canvas (DIC) — as **aiify-opp-6118** (commit `026fe26d4`):

- `tools/document_intelligence/ingest_orchestrator.py`:
  - `_ai_ocr_cleanup(text)` — bounded, grounded LLM correction of noisy OCR text
    (def L529; wired L1022-1027, gated on `extraction.provider in _OCR_PROVIDERS`).
  - `IngestOutcome.ocr_cleaned` flag (L311, set L1233).
  - `clean_ocr` ingest flag, default `True` (L952).
- Tests: `tests/test_dic_ingest_ocr_cleanup.py` — **12/12 pass**.

The card's literal "use claude-sonnet-4-6" ask is satisfied by routing OCR cleanup through
`LLMRouter` (model resolved from `.env`, never hardcoded — CLAUDE.md rule).

## Verification (2026-06-05)

- `git merge-base --is-ancestor 026fe26d4 HEAD` → **true** (irad/feature).
- `_ai_ocr_cleanup` / `ocr_cleaned` / `clean_ocr` present in `ingest_orchestrator.py` at HEAD.
- `pytest tests/test_dic_ingest_ocr_cleanup.py` → **12 passed**.

**Outcome:** No new code required. Closed as **dup of 6118**; card moved to done with
`bypass_verification:true` + `bypass_reason`.
