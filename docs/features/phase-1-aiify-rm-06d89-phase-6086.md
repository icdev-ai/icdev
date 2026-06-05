# CUI // SP-CTI

# Phase 1 — AI-ify: LLM Metadata Extraction for `paperless-ngx` `src/documents/views.py`

**Task:** `aiify-opp-6086`
**Opportunity:** 6086 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `metadata_extraction` → **`llm_generation`**
**Phase:** P1 — Quick Wins
**Recommended model:** `claude-sonnet-4-6`
**Scores:** composite 0.703 · value 0.806 · feasibility 0.845 · risk 0.775

> **Scope note — advisory, external repo.** Scan 43 targets the third-party
> open-source project **paperless-ngx** (`https://github.com/paperless-ngx/paperless-ngx`),
> cloned into an ephemeral temp directory that no longer exists. ICDEV does **not**
> own or vendor this code, so the deliverable for this opportunity is an
> **augmentation design recommendation**, not a code change to the ICDEV repo.
> This document is the artifact: a concrete, implementable plan a downstream
> adopter (or the upstream maintainers) could follow to realize the augmentation.
> No ICDEV runtime behavior changes.

## Opportunity

The scanner flagged `src/documents/views.py:1135–1183` (`function_name: <unknown>`)
with: *"Manual metadata parsing / assignment — replace with NLP entity extractor."*
In paperless-ngx this region is document-metadata assignment handling — the path
where a document's structured fields (title, correspondent, document type,
storage path, tags, and created/issue date) are derived and assigned. Today that
derivation leans on deterministic matching (regex/keyword `MatchingModel` rules,
filename/date heuristics) and manual user correction. The augmentation proposes
an LLM entity-extraction step that proposes structured metadata from the
already-available OCR'd document content, leaving the deterministic rules as a
fast-path/fallback and keeping a human in the loop for low-confidence fields.

## Recommended Augmentation

**Paradigm:** `llm_generation` (structured extraction / "NLP entity extractor").
**Model:** `claude-sonnet-4-6` — long context fits full OCR text; strong at
structured JSON extraction; cost-appropriate for a P1 quick win.

### 1. Extractor module (new)

`src/documents/ai_metadata.py` — a single `extract_metadata(text, candidates) -> MetadataSuggestion`:

- **Input:** the document's OCR `content` (truncated to a configurable char
  budget), plus the *existing* candidate sets already loaded by paperless
  (known correspondents, document types, tags, storage paths) so the model
  selects from real IDs instead of inventing free-text values.
- **Prompt:** a single hard-prompt template instructing the model to return a
  strict JSON object — `{title, correspondent_id|null, document_type_id|null,
  tag_ids[], created_date|null, confidence: {field: 0..1}}` — and to return
  `null` / empty rather than guess when evidence is weak. Provide the candidate
  lists as enumerated options; forbid values outside them for the ID fields.
- **Output:** validated against a `MetadataSuggestion` dataclass; any field
  whose confidence is below `min_confidence` is dropped (left for the
  deterministic path / user).

### 2. Wiring (minimal, non-breaking)

- Invoke the extractor in the consume/assign pipeline only when the existing
  deterministic matchers leave a field unset (fast-path first, LLM as the
  fallback "second opinion"). This bounds cost and preserves current behavior
  when rules already match.
- Persist suggestions as **proposals**, not silent writes: surface them in the
  existing document-edit UI as pre-filled-but-confirmable values. High-confidence
  fields may auto-apply behind a config flag; everything else is HITL.
- Reuse paperless' existing async task queue (Celery) so extraction never blocks
  the request path.

### 3. Configuration

All behavior toggled via paperless settings / env (no hardcoded model IDs):

| Setting | Purpose | Default |
|---|---|---|
| `PAPERLESS_AI_METADATA_ENABLED` | master switch | `false` |
| `PAPERLESS_AI_METADATA_MODEL` | model id | `claude-sonnet-4-6` |
| `PAPERLESS_AI_METADATA_MIN_CONFIDENCE` | drop-below threshold | `0.70` |
| `PAPERLESS_AI_METADATA_MAX_CHARS` | OCR truncation budget | `12000` |
| `PAPERLESS_AI_METADATA_AUTOAPPLY` | auto-apply high-confidence | `false` |

### 4. Safeguards (maps to ICDEV AI-security posture)

- **Grounded extraction** — model picks from real candidate IDs; free-text only
  for `title`. Prevents hallucinated correspondents/types.
- **Confidence gating + HITL** — sub-threshold fields fall back to the existing
  deterministic path; nothing auto-writes unless explicitly enabled.
- **Graceful degradation** — provider/timeout/parse failure → log and fall back
  to current manual parsing; never block ingestion.
- **No PII egress without opt-in** — disabled by default; document content is
  sent to the LLM only when the operator turns it on (relevant for self-hosted
  privacy-conscious paperless users).

## Why this is a P1 "Quick Win"

- **High feasibility (0.845):** additive module + one conditional call site; the
  candidate data and OCR text are already in hand at the assignment point.
- **High value (0.806):** removes the most tedious manual step (typing
  metadata), where rule-based matchers fall short on novel correspondents/types.
- **Bounded risk (0.775):** off by default, fallback-only invocation, HITL
  confirmation, and no change to the deterministic path's existing behavior.

## Acceptance Criteria (for a downstream implementer)

1. With the feature **disabled**, document assignment behaves exactly as today
   (zero behavioral delta; no LLM calls).
2. With it **enabled**, a document whose correspondent/type the rules *cannot*
   match yields a structured suggestion drawn only from existing candidate IDs.
3. Low-confidence (< `min_confidence`) fields are not applied and fall back to
   the manual path.
4. Provider failure degrades to the current manual parser without raising.
5. No model ID is hardcoded in Python — all read from settings/env.

## Status

Advisory design recommendation produced and recorded. No ICDEV code change is
warranted (target is an external project). Opportunity closed as **designed**.
