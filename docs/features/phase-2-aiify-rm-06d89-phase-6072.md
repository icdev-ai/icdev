# CUI // SP-CTI

# Phase 2 — AI-ify: LLM Metadata Extraction for `paperless-ngx` `src/documents/signals/handlers.py`

**Task:** `aiify-rm-06d89-phase-6072`
**Opportunity:** 6072 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `metadata_extraction` → **`llm_generation`**
**Phase:** P2 — Core Modernization
**Recommended model:** `claude-sonnet-4-6`
**Scores:** composite 0.682 · value 0.758 · feasibility 0.845 · risk 0.775

## Resolution — duplicate of `aiify-opp-6086` (already implemented)

Scan 43 flagged the same `metadata_extraction → llm_generation` augmentation in
two adjacent regions of the third-party **paperless-ngx** documents subsystem:

| Opportunity | Flagged path | Region |
|---|---|---|
| `6086` | `src/documents/views.py:1135–1183` | document-metadata **assignment** |
| `6072` (this task) | `src/documents/signals/handlers.py` | document-metadata **parsing on the consume/save signal** |

Both are the *same* capability — "manual metadata parsing/assignment → replace
with an NLP entity extractor" — at two ends of one pipeline (the signal handler
that fires on consume, and the view that assigns the result). The recommended
augmentation, the grounding/safety design, the model, and all four scores are
materially identical. Per the established `aiify-opp` duplicate-collision rule
([[aiify-duplicate-opportunities-collision]]) the right action is to **verify
and close as a duplicate**, not author a competing copy.

The paperless repo is ephemeral (the temp clone
`…/aiify_git_zwu66zfu/…` was deleted after the scan), so — per the standing
external-repo pattern ([[aiify-external-repo-opps-land-in-dic]]) — the
augmentation already landed in the analogous ICDEV subsystem, the **Document
Intelligence Canvas (DIC)**, under opportunity 6086.

## What already exists (no new code for 6072)

`tools/document_intelligence/ingest_orchestrator.py` —
`_ai_metadata_extraction(text, filename)` (added for `aiify-opp-6086`), invoked
during `ingest_file` behind the `extract_metadata=True` flag. It proposes
structured metadata from the document's own text:

- **`document_type`** — constrained to the closed `_METADATA_DOC_TYPES` enum;
  anything outside it collapses to `"other"` (the model cannot invent a type).
- **`tags`** — topic keywords drawn from the text; lower-cased, de-duplicated,
  length-capped (`_METADATA_TAG_MAX_LEN`) and count-capped (`_METADATA_MAX_TAGS`).
- **`date`** — kept only when it is a real ISO (`YYYY-MM-DD`) calendar date.
- **`confidence`** — a single 0..1 score gates the whole suggestion; below
  `_METADATA_MIN_CONFIDENCE` (0.70) the result is dropped for the HITL path.

Grounding & safety mirror the 6086 design and the ICDEV AI-security posture:
leading-`_METADATA_INPUT_CHARS` input bound, temperature 0.0,
`claude-sonnet-4-6` via the `summarization` LLM function, and silent degradation
to `None` on empty input, garbled output, or provider failure (ingestion never
breaks). The result is surfaced as a **HITL proposal** on
`IngestOutcome.metadata` (and `to_dict()`) — never silently written to
`dic_documents`.

## Verification

`tests/test_dic_ingest_metadata.py` — **17 tests pass** (enum constraint,
confidence gate, ISO-date validation, tag normalization/cap, input bound,
fenced-block tolerance, and every silent-fallback path). Re-run for this task:

```
$ python -m pytest tests/test_dic_ingest_metadata.py -q
17 passed
```

## Status

Closed as **duplicate — already implemented** under `aiify-opp-6086`. No new
ICDEV runtime behavior; existing DIC metadata-extraction capability verified
green and confirmed to cover 6072's region (the consume/save signal handler) as
the same end-to-end pipeline.
