<!-- CUI // SP-CTI -->

# AI-ify Opportunity 6109 — `ocr_extraction_pipeline` → `llm_generation` (dup of 6118)

- **Kanban task:** `aiify-rm-06d89-phase-6109`
- **Roadmap:** `rm-06d89040cf` (Phase 2 — Core Modernization), scan_id 43
- **Opportunity:** 6109
- **External target:** `…/aiify_git_zwu66zfu/src/paperless/parsers/tesseract.py` (paperless-ngx clone)
- **Pattern / paradigm:** `ocr_extraction_pipeline` → `llm_generation`
- **Recommended model:** `claude-sonnet-4-6`
- **Scores:** composite 0.6785 · value 0.7505 · feasibility 0.845 · risk 0.775
- **Determination:** **Duplicate of aiify-opp-6118** — no new implementation required.

## Why this is a duplicate

The `module_path` points at a shallow clone of an external open-source repo
(paperless-ngx) that the AI-ify engine creates, scans, and deletes. The external
file is not part of ICDEV and cannot be modified. Per the established disposition,
this paradigm is AI-ified in the **analogous ICDEV subsystem** — the Document
Intelligence Canvas (DIC) ingest pipeline.

The `ocr_extraction_pipeline` → `llm_generation` opportunity re-emits on every
scan against various paperless OCR/document files. It was first implemented under
**aiify-opp-6118** as `_ai_ocr_cleanup` in
`tools/document_intelligence/ingest_orchestrator.py` (commit `026fe26d4`).
`tesseract.py` is the **exact same file** already targeted by the closed
`aiify-rm-06d89-phase-6102`, `-6107`, and `-6108` cards — 6109 is the seventh
re-emission of the same opportunity against the same parser:

| Closed card | Flagged path | Disposition |
|---|---|---|
| `5987` | OCR pipeline | dup of 6118 |
| `6007` | OCR pipeline | dup of 6118 |
| `6078` | OCR pipeline | dup of 6118 |
| `6102` | `tesseract.py` | dup of 6118 |
| `6107` | `tesseract.py` | dup of 6118 |
| `6108` | `tesseract.py` | dup of 6118 |
| `6109` (this) | `tesseract.py` | **dup of 6118** |

The LLM-generation analog of running Tesseract is already covered twice in DIC:
the vision-LLM OCR fallback (`_vision_ocr` / `_ocr_image` in
`tools/document_intelligence/extractors.py`) performs OCR via a vision-capable
model, and `_ai_ocr_cleanup` (6118) corrects the noisy OCR output before it is
chunked and embedded. Authoring a competing copy would violate the standing
duplicate-collision rule.

## Verification at HEAD (irad/feature, HEAD `40537e7b5`)

- `026fe26d4` confirmed **ancestor of HEAD**.
- `_ai_ocr_cleanup(text)` present — `ingest_orchestrator.py:529`.
- Wired into ingest — `ingest_orchestrator.py:1021-1027` (gated on
  `extraction.provider in _OCR_PROVIDERS` and the `clean_ocr` flag).
- `IngestOutcome.ocr_cleaned` field — `ingest_orchestrator.py:311` (set L1027,
  surfaced in `to_dict()` L329).
- `clean_ocr: bool = True` default — `ingest_orchestrator.py:952`.
- Vision-LLM OCR analog — `extractors.py:_vision_ocr` / `_ocr_image`.
- Tests: `tests/test_dic_ingest_ocr_cleanup.py` present.

The card's literal "use claude-sonnet-4-6" ask is satisfied by routing OCR
cleanup through `LLMRouter` with the model selected from `.env` (CLAUDE.md
no-hardcoded-model-ID rule) — not by a hardcoded model string.

## Disposition

Card moved to **done** with `bypass_verification: true` and a `bypass_reason`
naming commit `026fe26d4` and the existing test verification. No code change to
the orchestrator — implementation already shipped under 6118.
