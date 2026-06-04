# CUI // SP-CTI

# Phase 2 — AI-ify rm-6efad / Opportunity 5486: Adaptive thresholds in the RAG ingestion manager

**Roadmap:** rm-6efad73721 · **Phase:** Phase 2 — Core Modernization
**Pattern:** `hardcoded_threshold` → `anomaly_detection` paradigm
**Module:** `tools/rag/ingestion_manager.py`
**Status:** Shipped 2026-06-03

## Problem

The RAG ingestion manager carried two undocumented inline magic numbers — the
embedding batch size (`batch_size = 20` in `_embed_chunks`) and the SQLite
`PRAGMA busy_timeout=5000`. The batch size in particular is throughput-sensitive:
a fixed 20 is wasteful for tiny corpora and sub-optimal for high-volume sources.
Neither value was discoverable as a tuning surface, and there was no signal when
an ingestion run skipped (deduped) an anomalous fraction of its chunks.

## Change

1. Extracted the inline literals into named, commented module-level fallback
   constants: `_EMBED_BATCH_SIZE` (20), `_DB_BUSY_TIMEOUT_MS` (5000),
   `_SKIP_RATE_ANOMALY` (0.95).
2. Added `_compute_ingestion_thresholds(anomaly_cfg)` — an adaptive calibrator
   (mirrors the existing `crag_evaluator` / `query_classifier` anomaly helpers)
   that reads historical `rag_ingestion_log` data to:
   - scale the embed batch size toward observed average chunks-per-ingestion,
     bounded by `[batch_floor, batch_ceil]`;
   - derive a `skip_rate_anomaly` threshold from the historical dedup skip-rate.
   Falls back to the module defaults when fewer than `min_samples` rows exist,
   when disabled, or on any DB error (never raises).
3. Wired the computed batch size into `ingest_source` and `ingest_single_record`
   via the new optional `_embed_chunks(..., batch_size=…)` parameter; replaced the
   PRAGMA literal with `_DB_BUSY_TIMEOUT_MS`.
4. `ingest_source` now returns `embed_batch_size`, `skip_rate`, and a
   `skip_rate_anomaly` boolean flag alongside the existing stats. The deterministic
   ingestion result remains authoritative — the anomaly flag is additive.
5. Added config block `ingestion_manager.anomaly_detection` to
   `args/rag_config.yaml` (enabled, min_samples, fallbacks, adaptive_bounds).

## Tests

`tests/genesis/test_ingestion_manager_anomaly.py` — 14 unit tests: constant
invariants; `_compute_ingestion_thresholds` disabled / insufficient-history /
high-throughput scale-up / low-throughput floor / ceiling clamp / skip-rate
bounds / DB-error fallback paths; `_load_anomaly_cfg` shape; and
`_embed_chunks` batch-size honouring. All passing; `ruff check` clean.

## Acceptance

- [x] Inline `batch_size`/`busy_timeout` literals replaced by named constants
- [x] Adaptive `_compute_ingestion_thresholds` reads history, falls back safely
- [x] Computed batch size consumed by both ingest paths
- [x] Config override surface added under `ingestion_manager.anomaly_detection`
- [x] 14 tests passing
- [x] Lint clean
- [x] Opportunity 5486 closed
