# CUI // SP-CTI

# Phase 2 — aiify-opp-6043: `manual_classification_ui` → `llm_generation`

**Roadmap:** rm-06d89040cf · **Scan:** 43 · **Opportunity:** 6043
**Phase:** Phase 2 — Core Modernization
**Model recommendation:** claude-sonnet-4-6
**Scores:** composite 0.6481 · value 0.683 · feasibility 0.845 · risk 0.775

## Source opportunity

The external AI-ify scan flagged paperless-ngx `src/documents/models.py` — the
`Correspondent` / `DocumentType` / `Tag` models and their `matching_algorithm`
(`ANY` / `ALL` / `LITERAL` / `REGEX` / `FUZZY` / `AUTO`) + `match` fields. Those
back paperless's **manual-classification UI**: a user hand-curates a taxonomy of
labels and writes per-label matching rules so each new document is filed under an
*existing* category. The recommendation is to replace that hand-tuned rule
machinery with LLM generation.

The scanned repository is ephemeral (the engine clones it to a temp path and
deletes it), so — per the established aiify-opp pattern — the augmentation lands
in the analogous ICDEV subsystem: the **Document Intelligence Canvas (DIC)**,
`tools/document_intelligence/`.

## What shipped

A new opt-in enrichment in the DIC ingest orchestrator
(`tools/document_intelligence/ingest_orchestrator.py`):

- **`_ai_classify_into_taxonomy(text, taxonomy, *, multi_label=False, filename="")`**
  — the LLM-generation analog of `matching_algorithm = AUTO`. Given the document
  text and a **caller-supplied, user-curated taxonomy of existing labels**, the
  model softly files the document under one (single-label, the Correspondent /
  DocumentType analog) or several (multi-label, the Tag analog) of those labels.
- **`_normalize_taxonomy(taxonomy)`** — trims, drops blanks, length-caps,
  case-insensitively de-duplicates (first-seen casing wins) and bounds the
  candidate list to `_CLASSIFY_MAX_LABELS`.
- Wired into `ingest_file(...)` behind two new keyword args:
  `classify_taxonomy: list[str] | None = None` (default `None` ⇒ feature off) and
  `classify_multi_label: bool = False`. When a taxonomy is supplied the proposal
  is surfaced under `IngestOutcome.metadata["classification"]`
  (`{"labels": [...], "confidence": float}`).

### Why this is distinct from aiify-opp-6086 (not a duplicate)

`6086` (`metadata_extraction` from `views.py`) performs **open-vocabulary**
extraction: a `document_type` from a *fixed module-level enum*, free topic tags,
and a date — it proposes *new* metadata derived from the text.

`6043` (this opp, `manual_classification_ui` from `models.py`) is **closed-set
classification into a caller-supplied, dynamic taxonomy of existing labels**. The
model may only *select* from the labels it is given and returns `unmatched` when
none fit. It models the "file this document under one of my existing
correspondents/types/tags" UI action and the `AUTO` match rule — a different
source file, a different pattern type, and a non-overlapping capability.

## Grounding & safety (mirrors the 6086 / 5988 design and the ICDEV AI-security posture)

- **No fabrication.** The model's picks are intersected back against the exact
  offered taxonomy (case-insensitive); any label not offered — including the
  `unmatched` sentinel — is dropped. The model can never invent a category.
- **Canonical casing** from the caller's taxonomy is restored on every match.
- **Bounded selection.** Single-label keeps only the top pick; multi-label is
  de-duplicated and capped at `_CLASSIFY_MAX_SELECTED`.
- **Confidence gate.** Below `_CLASSIFY_MIN_CONFIDENCE` (0.70) the whole
  suggestion is discarded for the HITL / manual path, as is an empty/`unmatched`
  selection.
- **Bounded input.** Only the leading `_CLASSIFY_INPUT_CHARS` (6000) of text and
  a bounded taxonomy reach the model — cheap and size-independent.
- **HITL only.** The result is a *proposal* on `IngestOutcome.metadata`, never
  silently written to `dic_documents`; a human confirms the filing.
- **Air-gap safe.** Any failure / unavailability — empty text, empty taxonomy,
  blank or garbled output, missing confidence, provider down — degrades to
  `None` and ingestion proceeds unchanged. Feature is off unless a taxonomy is
  explicitly supplied.

## Tests

`tests/test_dic_ingest_classify.py` — 21 tests covering taxonomy normalization
(trim/de-dupe/order, length-cap, count-cap) and the classifier: single- vs
multi-label, case-insensitive match with canonical-casing restore, fabricated
label rejection, mixed valid/fabricated, `unmatched`/empty fallback, confidence
gate (below / at threshold / missing), empty text & empty taxonomy short-circuit
(no LLM call), fenced-block tolerance, garbled output, input truncation,
candidate-label inclusion in the prompt, and LLM-failure degradation.

All 21 pass; the existing `6086` metadata and `5988` identifier suites (37 tests)
remain green; `ruff` clean.

## Files changed

- `tools/document_intelligence/ingest_orchestrator.py` — new helpers +
  `ingest_file` wiring.
- `tests/test_dic_ingest_classify.py` — new test suite.
- `docs/features/phase-2-aiify-rm-06d89-phase-6043.md` — this doc.
