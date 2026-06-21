<!-- CUI // SP-CTI -->

# AI-ify Opportunity 6107 — `ocr_extraction_pipeline` → `llm_generation` (dup of 6118)

- **Kanban task:** `aiify-rm-06d89-phase-6107`
- **Roadmap:** `rm-06d89040cf` (Phase 2 — Core Modernization), scan_id 43
- **Opportunity:** 6107
- **External target:** `…/aiify_git_zwu66zfu/src/paperless/parsers/tesseract.py` (paperless-ngx clone)
- **Pattern / paradigm:** `ocr_extraction_pipeline` → `llm_generation`
- **Determination:** **Duplicate of aiify-opp-6118** — no new implementation required.

## Why this is a duplicate

The `module_path` points at a shallow clone of an external open-source repo
(paperless-ngx) that the AI-ify engine creates, scans, and deletes. The external
file is not part of ICDEV and cannot be modified. Per the established disposition,
this paradigm is AI-ified in the **analogous ICDEV subsystem** — the Document
Intelligence Canvas (DIC) ingest pipeline.

The `ocr_extraction_pipeline` → `llm_generation` opportunity re-emits on every
scan against various paperless OCR/document files (opps 3765/4190/4612/4775/4974/
5157/6118; roadmap phases 5987/6078/6102 against `tesseract.py`/`barcodes.py`/
`utils.py`). It was first implemented under **aiify-opp-6118** as
`_ai_ocr_cleanup` in `tools/document_intelligence/ingest_orchestrator.py`
(commit `026fe26d4`). `tesseract.py` is the exact same file targeted by the
already-closed `aiify-rm-06d89-phase-6102`.

## Verification at HEAD (irad/feature, HEAD `92ce8ecb0`)

- `026fe26d4` confirmed **ancestor of HEAD**.
- `_ai_ocr_cleanup(text)` present — `ingest_orchestrator.py:529`.
- Wired into ingest — `ingest_orchestrator.py:1022-1027` (gated on
  `extraction.provider in _OCR_PROVIDERS`, `clean_ocr` flag).
- `IngestOutcome.ocr_cleaned` — `ingest_orchestrator.py:311` (set L1027, surfaced L1233).
- `clean_ocr: bool = True` default — `ingest_orchestrator.py:952`.
- Tests: `tests/test_dic_ingest_ocr_cleanup.py` — **12/12 pass**.

The card's literal "use claude-sonnet-4-6" ask is satisfied by routing OCR
cleanup through `LLMRouter` with the model selected from `.env` (CLAUDE.md
no-hardcoded-model-ID rule) — not by a hardcoded model string.

## Disposition

Card moved to **done** with `bypass_verification: true` and a `bypass_reason`
naming commit `026fe26d4` and the 12-passing test verification. No code change to
the orchestrator — implementation already shipped under 6118.
