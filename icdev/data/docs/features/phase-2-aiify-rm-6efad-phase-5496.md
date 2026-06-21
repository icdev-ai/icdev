# Phase 2 — AI-ify rm-6efad-phase-5496: Adaptive Anomaly Detection for Quality Feedback Loop

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Opportunity | 5496 (scan 28, roadmap rm-6efad73721) |
| Phase | Phase 2 — Core Modernization |
| Pattern | `hardcoded_threshold` → `anomaly_detection` |
| Module | `tools/rag/quality_feedback_loop.py` |
| Composite score | 0.5326 (value 0.491, feasibility 0.7475, risk 0.75) |

## Summary

Replaces the closed-loop RAG quality feedback pipeline's reliance on frozen magic
numbers with **named, config-overridable constants** plus **adaptive anomaly
detection**, mirroring the sibling modernization of `tools/rag/evaluator.py`
(phase-5480).

## Changes

### `tools/rag/quality_feedback_loop.py`
- Extracted hardcoded literals to module-level fallback constants, all overridable
  from `args/finetune_config.yaml` under `quality_feedback`:
  - `_MAX_AUTO_PAIRS_PER_CYCLE = 50`
  - `_MIN_PAIRS_PER_SOURCE = 5`
  - `_ID_HEX_LEN = 12`
  - `_METRIC_ANOMALY_FLOORS` (ndcg / mrr / avg_retrieval_score) and
    `_ANOMALY_STDDEV_K = 2.0`
- `_compute_feedback_anomaly_thresholds()` — derives per-metric quality floors as
  `mean − k·stddev` of historical `ft_quality_snapshots.metric_value` (population
  stddev via `E[x²] − E[x]²`, since SQLite has no `STDDEV()`). Floors are clamped
  by `adaptive_bounds`; falls back to module floors when fewer than `min_samples`
  rows exist for a metric, when detection is disabled, or on any DB error.
- `flag_quality_anomaly()` — flags a quality-metrics snapshot as anomalous when any
  present metric falls below its (adaptive) floor; returns
  `{"anomalous", "reasons", "floors"}`. Tolerates missing / `None` / non-numeric
  values.
- `run_feedback_cycle()` now surfaces an `anomaly_detection` block and appends a
  `quality_anomaly_detected` action when a regression is flagged. `get_feedback_status()`
  surfaces the same block. The static `retrain_recommended` gate is preserved (the
  anomaly signal is additive/observational), so existing remediation behavior is
  unchanged.

### `args/finetune_config.yaml`
- `quality_feedback.anomaly_detection` block (`enabled`, `min_samples`, `stddev_k`,
  `fallback_floors`, `adaptive_bounds`) — added under sibling phase-5497.

### `tests/test_quality_feedback_loop.py`
- `TestAnomalyThresholds`, `TestFlagQualityAnomaly`, `TestCycleAnomalyIntegration` —
  cover disabled/sparse fallback, config-override of floors, below-floor flagging,
  None/non-numeric tolerance, and cycle integration.

## Verification

- `pytest tests/test_quality_feedback_loop.py -v` → **18 passed**
- `ruff check` → clean

## Why it matters

A static `if score < 0.5` threshold goes stale as the corpus and embedding model
evolve. Deriving floors from observed history keeps the definition of "degraded"
tracking the data, reducing both false retrains (drifted-up baselines) and missed
regressions (drifted-down baselines), while remaining air-gap safe (pure SQL stats,
no LLM call) and falling back deterministically when history is sparse.
