# CUI // SP-CTI

# Phase 2 — AI-ify: Anomaly Detection for `tools/rag/quality_feedback_loop.py`

**Task:** `aiify-rm-6efad-phase-5497`
**Opportunity:** 5497 (scan 28, roadmap `rm-6efad73721`)
**Pattern:** `hardcoded_threshold` → **`anomaly_detection`**
**Phase:** Phase 2 — Core Modernization
**Model recommendation:** `claude-haiku-4-5-20251001`

## Problem

`tools/rag/quality_feedback_loop.py` (closed-loop RAG quality → retrain
pipeline, D-KARL-9) drove its remediation decision off a binary
`retrain_recommended` flag and carried frozen magic numbers (per-cycle pair
budget `50`, per-source floor `5`, id hex length `12`) with no mechanism to
flag a cycle whose RAG metric scores regressed versus prior cycles. It was the
remaining `tools/rag/` file from the aiify sweep (`evaluator`,
`rag_to_kg_ingester`, `ingestion_manager`, `crag_evaluator`, …) without
adaptive anomaly detection.

## Change

Follows the canonical RAG aiify pattern (see
`evaluator._compute_eval_anomaly_thresholds`):

1. **Extracted constants** — module-level named constants, config-overridable
   from `args/finetune_config.yaml` under `quality_feedback`:
   `_MAX_AUTO_PAIRS_PER_CYCLE`, `_MIN_PAIRS_PER_SOURCE`, `_ID_HEX_LEN`,
   plus anomaly floors `_METRIC_ANOMALY_FLOORS` and `_ANOMALY_STDDEV_K`.
2. **Adaptive anomaly detection** — `_compute_feedback_anomaly_thresholds()`
   reads historical `metric_value` per metric from `ft_quality_snapshots` and
   derives a statistical lower bound `mean − k·stddev` (population stddev via
   `E[x²] − E[x]²`, since SQLite has no `STDDEV()`), bounded by
   `adaptive_bounds.floor_min/floor_max`. Falls back to the module floors when
   `< min_samples` rows exist for a metric or detection is disabled.
3. **`flag_quality_anomaly()`** — flags a cycle's quality metrics
   (`ndcg`, `mrr`, `avg_retrieval_score`) as a regression when they fall below
   the (adaptive) floor. Surfaced as an `anomaly_detection` block in both
   `run_feedback_cycle()` results (with a `quality_anomaly_detected` action)
   and `get_feedback_status()`.

## Config

`args/finetune_config.yaml` → `quality_feedback.anomaly_detection` (enabled,
min_samples 30, stddev_k 2.0, fallback floors 0.30, adaptive bounds 0.05–0.80)
plus `min_pairs_per_source: 5`.

## Tests

`tests/test_rag_quality_feedback_loop.py` — 15 unit tests (extracted constants,
adaptive-threshold fallback paths, `flag_quality_anomaly` boundary/missing/
non-numeric behavior, multi-metric flagging). All passing. `ruff check` clean.

## Acceptance

- No behavior change to remediation control flow; anomaly detection is additive
  signal surfaced in cycle/status output.
- A low-quality cycle (metric below the adaptive/fallback floor) now surfaces an
  explicit anomaly with reasons.
- Thresholds and budgets tunable from config without editing code.
