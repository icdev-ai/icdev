# CUI // SP-CTI

# Phase 2 — AI-ify: LLM Document-Identifier Extraction for `paperless-ngx` `src/documents/barcodes.py`

**Task:** `aiify-opp-5988`
**Opportunity:** 5988 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `ocr_extraction_pipeline` → **`llm_generation`**
**Phase:** P2 — Core Modernization
**Recommended model:** `claude-sonnet-4-6`
**Scores:** composite 0.6785 · value 0.7505 · feasibility 0.845 · risk 0.775

> **Scope note — advisory, external repo.** Scan 43 targets the third-party
> open-source project **paperless-ngx** (`https://github.com/paperless-ngx/paperless-ngx`),
> cloned into an ephemeral temp directory that no longer exists. ICDEV does **not**
> own or vendor this code, so the deliverable for this opportunity is an
> **augmentation design recommendation** plus an implementation in the analogous
> ICDEV subsystem (DIC). No paperless-ngx runtime is modified.

## Opportunity

The scanner flagged `src/documents/barcodes.py` (`function_name: <unknown>`) — the
barcode/QR reader of paperless-ngx's OCR/consume pipeline. That module scans a
page image for machine-readable codes to (a) **split** a multi-page scan into
separate documents at *separator* barcodes and (b) assign an **Archive Serial
Number (ASN)** from a barcode value. It is purely a computer-vision barcode
decode — it does nothing when a document carries no physical barcode, even though
the very same identifiers (ASN, invoice/contract/PO/reference numbers) are very
often **printed in the document text** that OCR has already recovered.

## Recommended Augmentation

**Paradigm:** `llm_generation` — the LLM analog of the barcode reader: extract the
structured identifiers a barcode *would* carry from the OCR'd text when no
physical barcode is present.
**Model:** `claude-sonnet-4-6` — long context fits full OCR text; strong at
constrained structured-JSON extraction; cost-appropriate for a P2 item.

### 1. Extractor (new, additive)

A single `extract_identifiers(text) -> list[Identifier]` that proposes the codes
printed in the document:

- **Input:** the document's OCR `content` (truncated to a configurable char
  budget). No external context — grounded on the document's own text.
- **Prompt:** a hard-prompt template instructing the model to return a strict
  JSON object `{identifiers: [{kind, value, confidence}], confidence}`, where
  `kind` is drawn from a closed enum and `value` is a code copied
  character-for-character from the text. Return an empty list rather than guess.
- **Output:** each value is shape-validated (compact code, not prose) and its
  alphanumeric core must appear verbatim in the source text — a hard
  anti-hallucination guard so the model can only surface codes actually printed
  on the document. Sub-threshold items are dropped.

### 2. Wiring (minimal, non-breaking)

- Invoke as a **fallback / second source** alongside the physical-barcode reader:
  the CV decode remains the fast path; the LLM only adds identifiers the barcode
  reader could not produce (no barcode present).
- Persist results as **proposals**, not silent writes — surface as
  confirmable values (e.g. a suggested ASN) so a human approves before they
  stick. High-confidence values may auto-apply behind a config flag.
- Run on the existing async task queue so it never blocks the consume path.

### 3. Configuration

| Setting | Purpose | Default |
|---|---|---|
| `PAPERLESS_AI_IDENTIFIERS_ENABLED` | master switch | `false` |
| `PAPERLESS_AI_IDENTIFIERS_MODEL` | model id | `claude-sonnet-4-6` |
| `PAPERLESS_AI_IDENTIFIERS_MIN_CONFIDENCE` | drop-below threshold | `0.70` |
| `PAPERLESS_AI_IDENTIFIERS_MAX_CHARS` | OCR truncation budget | `12000` |

### 4. Safeguards (maps to ICDEV AI-security posture)

- **Grounded extraction** — value's alphanumeric core must literally appear in
  the source text; `kind` is a closed enum. The model cannot invent a code.
- **Shape guard** — values must match a compact identifier pattern, never prose.
- **Confidence gating + HITL** — sub-threshold items dropped; nothing auto-writes
  unless explicitly enabled.
- **Graceful degradation** — provider/timeout/parse failure → log and fall back
  to the barcode-only path; never block ingestion.

## Acceptance Criteria (for a downstream implementer)

1. With the feature **disabled**, the barcode pipeline behaves exactly as today
   (zero behavioral delta; no LLM calls).
2. With it **enabled**, a document with no physical barcode but a printed ASN /
   reference code yields a structured identifier proposal.
3. A code the model emits that is **not** present in the text is rejected.
4. Low-confidence (< `min_confidence`) items are not applied.
5. Provider failure degrades to the barcode-only path without raising.
6. No model ID is hardcoded in Python — all read from settings/env.

## Status

Advisory design recommendation produced for the external paperless-ngx target
(above). Per the established `aiify-opp` pattern — the scan repo is ephemeral, so
the augmentation lands in the **analogous ICDEV subsystem** — the capability was
**implemented in the Document Intelligence Canvas (DIC)**, mirroring siblings
`aiify-opp-6098` (title/abstract), `aiify-opp-6118` (OCR cleanup), and
`aiify-opp-6086` (metadata).

### Implemented (ICDEV / DIC)

`tools/document_intelligence/ingest_orchestrator.py` — new
`_ai_extract_identifiers(text)` helper invoked during `ingest_file` (new
`extract_identifiers=True` flag). It extracts the codes a barcode/label would
carry from the document's own OCR'd text:

- **`kind`** — constrained to a closed enum (`_IDENTIFIER_KINDS`: `asn`,
  `invoice_number`, `contract_number`, `po_number`, `reference_number`,
  `document_number`, `tracking_number`, `control_number`, `case_number`,
  `serial_number`); anything outside it is dropped.
- **`value`** — must match the compact identifier shape
  (`_IDENTIFIER_VALUE_RE`, alphanumerics + `-` `/` `.` `#`, length-capped at
  `_IDENTIFIER_VALUE_MAX_LEN`), and its alphanumeric core must appear verbatim
  in the source text (anti-hallucination membership guard, tolerant of OCR
  spacing/separator differences).
- **`confidence`** — a per-item score plus a single overall score gate; below
  `_IDENTIFIER_MIN_CONFIDENCE` (0.70) the item / whole suggestion is dropped for
  the HITL / manual path. Items are de-duplicated by `(kind, value)` and
  count-capped (`_IDENTIFIER_MAX_ITEMS`).

Grounding & safety match the design: leading-`_IDENTIFIER_INPUT_CHARS` input
bound, temperature 0.0, `claude-sonnet-4-6` via the `summarization` LLM function,
and silent degradation to `None` on empty input, garbled output, or provider
failure (ingestion never breaks). Results are surfaced as a **HITL proposal**
under `IngestOutcome.metadata["identifiers"]` (and `to_dict()`) — never silently
written to `dic_documents`. Covered by `tests/test_dic_ingest_identifiers.py`
(20 tests pinning the enum constraint, shape guard, in-text grounding guard,
per-item + overall confidence gates, de-dup, count cap, input bound,
fenced-block tolerance, and every silent-fallback path). Opportunity closed as
**implemented**.
