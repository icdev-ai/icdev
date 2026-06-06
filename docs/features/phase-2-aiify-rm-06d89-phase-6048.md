# CUI // SP-CTI

# Phase 2 — AI-ify: Date-Parsing Anomaly Detection for `paperless-ngx` `src/documents/plugins/date_parsing/__init__.py`

**Task:** `aiify-opp-6048`
**Opportunity:** 6048 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → **`anomaly_detection`**
**Phase:** P2 — Core Modernization
**Recommended model:** `claude-haiku-4-5-20251001`
**Scores:** composite 0.547 · value 0.523 · feasibility 0.7475 · risk 0.75

> **Scope note — advisory, external repo.** Scan 43 targets the third-party
> open-source project **paperless-ngx**, cloned into an ephemeral temp directory
> (`aiify_git_zwu66zfu`) that no longer exists. ICDEV does **not** own or vendor
> this code, so the deliverable is an **augmentation design recommendation** plus
> an implementation in the analogous ICDEV subsystem (DIC). No paperless-ngx
> runtime is modified.

## Opportunity

The scanner flagged `src/documents/plugins/date_parsing/__init__.py` — the
consumer plugin that parses a document's date out of its OCR'd text/filename
during ingestion using **fixed parsing rules and threshold bounds**
(`hardcoded_threshold`). The recommended paradigm, `anomaly_detection`, is a
better fit: rather than trusting a single rule-parsed date, parse **every**
candidate date the document contains and flag the ones that are *anomalous* — a
date in the future, one implausibly far in the past, or a statistical outlier
relative to the document's own dates (the OCR typo that turns `2023` into `2093`,
a mis-scanned year, a back-dated insert).

## Recommended Augmentation

**Paradigm:** `anomaly_detection` — a deterministic, offline detector over the
document's parsed dates, with the fixed thresholds lifted into named, tunable
constants and an optional LLM layer grading only *severity*.
**Model:** `claude-haiku-4-5-20251001` — cheap, bounded severity judgement
appropriate for a P2 enrichment; never on the critical path.

## Status — Implemented (ICDEV / DIC)

Per the established `aiify-opp` pattern (ephemeral scan repo → land in the
analogous ICDEV subsystem), the capability was implemented in the **Document
Intelligence Canvas (DIC)**, complementing the single-date proposal of
`aiify-opp-6086` (`_ai_metadata_extraction`) and mirroring the freshness-anomaly
sibling `aiify-opp-6042`.

### `tools/document_intelligence/ingest_orchestrator.py`

New, additive, HITL-gated capability invoked during `ingest_file` (new
`detect_date_anomalies=True` flag):

- **`_parse_candidate_dates(text)`** — deterministic, offline regex parser for
  ISO (`2024-03-09`), US-slash/dot (`03/09/2024`), and long-form month-name
  (`March 9, 2024` / `9 March 2024`) dates. Each hit is validated as a *real*
  calendar date (rejects `2024-02-30`, month 13) and de-duplicated by ISO value,
  keeping the first textual occurrence. Only the leading `_DATE_INPUT_CHARS`
  (20 000) are scanned. No third-party `dateutil`.
- **`_detect_date_anomalies(parsed, now_iso=None)`** — the **always-authoritative**
  baseline. Three rules, each lifting the flagged `hardcoded_threshold` into a
  named constant:
  - `future_dated` — more than `_DATE_FUTURE_TOLERANCE_DAYS` (1) past now;
  - `implausibly_old` — calendar year before `_DATE_MIN_PLAUSIBLE_YEAR` (1900);
  - `cluster_outlier` — more than `_DATE_ANOMALY_STDEV_K` (2.0, in step with the
    freshness sibling so a lone strong outlier can't mask itself) standard
    deviations from the document's own date cluster, gated on at least
    `_DATE_ANOMALY_MIN_SAMPLE` (4) dates and a non-degenerate spread.
  Returns a JSON-clean summary (`total`, `anomaly_count`, `anomalies[]`,
  `mean_year`, `stdev_days`, `baseline_severity`).
- **`_ai_date_anomaly_assessment(summary, anomalies)`** — best-effort LLM layer
  (router function `dic_date_anomaly_assessment`) grading **severity only**,
  shown at most `_DATE_ANOMALY_LLM_SAMPLE` (6) flagged dates. Degrades silently
  to `None` (→ deterministic baseline) on empty input, blank/garbled output, or
  any provider failure.
- **`assess_document_dates(text, now_iso=None)`** — orchestrator; returns `None`
  when nothing is anomalous (nothing to surface), else the parsed dates, the
  anomalies, and a severity (LLM grade when available, else baseline). The LLM
  can never override the deterministic *detection*, only the severity label.

Wired into `ingest_file` as a **HITL proposal** under
`IngestOutcome.metadata["date_anomalies"]` (only when ≥1 anomaly is found) —
never silently written to `dic_documents`.

### Configuration

- `args/llm_config.yaml` — new `dic_date_anomaly_assessment` function
  (chain `claude-haiku, qwen3-local, gpt-4o-mini, llama-local`, effort `low`).
  No model ID hardcoded in Python.

## Acceptance Criteria

1. With `detect_date_anomalies` **disabled**, ingestion behaves exactly as today
   (no parsing, no LLM call). ✅
2. A future-dated, implausibly-old, or cluster-outlier date is deterministically
   flagged with the correct reason, **offline**. ✅
3. The statistical outlier rule does not fire below `_DATE_ANOMALY_MIN_SAMPLE`
   dates or on a uniform date set. ✅
4. The LLM grades severity only; provider failure / garbled output degrades to
   the deterministic baseline without raising. ✅
5. A clean document (no anomalies) yields `None` and never calls the LLM. ✅
6. The result serializes cleanly for the metadata proposal; no model ID is
   hardcoded in Python. ✅

## Tests

`tests/test_dic_ingest_date_anomaly.py` — **25 tests** covering multi-format
parsing, calendar-validity + de-dup + input-window bounds, each anomaly rule,
the min-sample / uniform no-flag cases, JSON-cleanliness, severity bands, the
LLM normalize/reject/fence/garble/failure/sample-bound paths, and the
orchestrator's none-when-clean / baseline-fallback / severity-override behavior.
Opportunity closed as **implemented**.
