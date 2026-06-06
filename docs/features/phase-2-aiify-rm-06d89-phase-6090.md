# Phase 2 — AI-ify DIC Anomaly Severity (opp-6090)

**Task:** `aiify-rm-06d89-phase-6090`
**Pattern:** `hardcoded_threshold -> anomaly_detection`
**Scan/Roadmap:** scan 43 · `rm-06d89040cf` · Phase 2 — Core Modernization
**Model:** `claude-haiku-4-5-20251001` (routing fn `dic_anomaly_severity`)

## Context

Opportunity 6090 named `…/aiify_git_zwu66zfu/src/documents/views.py` — a paperless
external repo the AI-ify scanner clones to a temp dir and deletes after scanning.
Per the established pattern (external-repo opps land in the analogous ICDEV
subsystem), the work was applied to the **Document Intelligence Canvas (DIC)**,
whose anomaly detection lives in `tools/document_intelligence/analytics_engine.py`.

## Problem

`detect_anomalies()` classified overall severity with inline magic numbers:

```python
severity = "low"
if len(contradictions) > 5 or len(stale_docs) > 2:
    severity = "high"
elif len(orphans) > 20 or len(single_source) > 10:
    severity = "medium"
```

Brittle, context-blind, and unexplained — the textbook `hardcoded_threshold`
smell.

## Change

- **Named constants** — the magic numbers become `_SEV_HIGH_CONTRADICTIONS`,
  `_SEV_HIGH_STALE_DOCS`, `_SEV_MEDIUM_ORPHANS`, `_SEV_MEDIUM_SINGLE_SOURCE`,
  consumed by a pure `_heuristic_severity(summary)` baseline.
- **LLM grade** — `_ai_anomaly_severity(summary, samples)` reasons over the real
  counts + a bounded (`_ANOMALY_SAMPLE = 5`) sample of concrete anomalies and the
  heuristic baseline, returning `{severity, rationale, top_concern}`.
- **Safety net** — the heuristic is always authoritative; any no-data, blank,
  malformed, out-of-range, or LLM-failure result degrades silently to it.
  Detection never depends on the model being reachable.
- **Routing** — `dic_anomaly_severity` added to `args/llm_config.yaml`
  (claude-haiku chain, effort low).
- **UI** — the analytics anomaly alert shows an `AI`/`heuristic` source badge and
  the AI rationale (both `tools/` and `icdev/` template mirrors).

## Return-shape additions

`detect_anomalies()` now also returns `severity_source` (`ai`|`heuristic`),
`severity_rationale`, `severity_top_concern`, and `heuristic_severity`. The
existing `severity` and `summary` keys are unchanged (backward compatible).

## Verification

`tests/test_dic_anomaly_severity.py` — 15 tests, all passing:
heuristic baseline (incl. missing-key tolerance), JSON parsing, fenced-block
tolerance, bounded sample, grounded baseline in prompt, and the full silent-fallback
matrix (no-data / blank / malformed / out-of-range / provider-down). `ruff` clean.
