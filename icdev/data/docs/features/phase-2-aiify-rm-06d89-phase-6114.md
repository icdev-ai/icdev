# Phase 2 — aiify-rm-06d89-phase-6114 (determination: duplicate of opp 6118)

- **Roadmap:** `rm-06d89040cf`, scan_id 43
- **Opportunity:** 6114
- **Pattern → paradigm:** `ocr_extraction_pipeline` → `llm_generation`
- **External module_path:** `…\aiify_git_zwu66zfu\src\paperless\serialisers.py`

## Determination

This opportunity targets an **external open-source repo** (paperless-ngx) that the
AI-ify engine shallow-clones into a temp `aiify_git_*` directory, scans, then deletes.
The file is unmodifiable by the time the card runs, and `serialisers.py` is just
another filename in the same paperless OCR subtree — **pattern + paradigm decide the
internal analog, not the path.**

The `ocr_extraction_pipeline → llm_generation` capability was already AI-ified in the
analogous ICDEV subsystem (**DIC**, `tools/document_intelligence/`) as **opp 6118**:

- `_ai_ocr_cleanup(text)` — best-effort LLM correction of noisy OCR text, gated to
  `extraction.provider in _OCR_PROVIDERS`, bounded by `_OCR_CLEANUP_MAX_CHARS`, with a
  0.5–2.0x length-ratio grounding guard; `clean_ocr` flag default True;
  `IngestOutcome.ocr_cleaned` surfaced; raw-OCR `content_sha256` preserves idempotency.

## Verification at HEAD (irad/feature)

- `026fe26d4` (`feat(aiify-opp-6118): add LLM OCR-text cleanup to DIC ingestion`) is an
  **ancestor of HEAD**.
- `tools/document_intelligence/ingest_orchestrator.py`: `_ai_ocr_cleanup` def L529,
  wired L1022–1027, `IngestOutcome.ocr_cleaned` L311/L1233, `clean_ocr` default True L952.
- `tests/test_dic_ingest_ocr_cleanup.py`: **12/12 pass**.

The card's literal ask (use `claude-sonnet-4-6`) is satisfied by the LLM Router routing
OCR cleanup through `LLMRouter().invoke("summarization", …)`, with the model selected
from `.env` — model IDs are never hardcoded in Python (CLAUDE.md rule).

**Disposition:** closed as **duplicate of 6118** (`026fe26d4`). No new code required.
