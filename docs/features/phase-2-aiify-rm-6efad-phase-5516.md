# Phase 2 — AI-ify: Adaptive Anomaly Detection for RAG Retention

<!-- CUI // SP-CTI -->

**Task:** `aiify-rm-6efad-phase-5516`
**Roadmap:** `rm-6efad73721` — Phase 2 (Core Modernization)
**Opportunity:** `5516` — `hardcoded_threshold` → `anomaly_detection`
**Module:** `tools/rag/retention_manager.py`
**Model recommendation:** `claude-haiku-4-5-20251001`

## Problem

`retention_manager.py` governed RAG chunk tier migration (hot → warm → cold)
with frozen magic numbers: `hot_days = 30` and `warm_days = 365` literals (plus
a hardcoded `"nomic-embed-text"` rehydration model). A chunk that lingered in a
tier far longer than its peers — overdue for migration because a scheduled run
was missed or its `created_at` skewed — sat silently; the static rule had no
way to surface it.

## Change

Mirrors the sibling pattern from `aiify-rm-6efad-phase-5513` (reranker): extract
the literals to named, config-overridable constants and add **adaptive anomaly
detection** as a separate, read-only surface — migration behaviour is unchanged.

### Extracted constants (module-level, all config-overridable)
- `_HOT_DAYS = 30`, `_WARM_DAYS = 365` — default tier ages
- `_REHYDRATE_EMBED_MODEL = "nomic-embed-text"` — cold-chunk re-embedding model
- `_ANOMALY_STDDEV_K = 2.0`, `_ANOMALY_MIN_SAMPLES = 20` — detection knobs

### New functions
- `_chunk_age_days(created_at, now=None)` — robust age-in-days parsing for
  ISO-8601 (with/without tz, trailing `Z`) and SQLite `CURRENT_TIMESTAMP`
  (`YYYY-MM-DD HH:MM:SS`) forms; future timestamps clamp to `0.0`.
- `_load_retention_anomaly_config(config=None)` — reads
  `rag.retention.anomaly_detection` (inline block preferred, else yaml file).
- `_compute_retention_anomaly_thresholds(tenant_id, anomaly_cfg)` — per-tier
  overdue-age threshold = `mean + k·stddev` of resident-chunk ages (population
  stddev via `E[x²] − E[x]²`, computed in Python → backend-agnostic). Clamped to
  `adaptive_bounds`; never drops below the configured tier age. Falls back to
  the static `hot_days`/`warm_days` when a tier has `< min_samples` chunks or
  detection is disabled.
- `detect_retention_anomalies(tenant_id)` — flags chunks whose age exceeds their
  tier's adaptive threshold (overdue-migration outliers). Returns per-tier IDs,
  counts, the thresholds used, and the `adaptive` flag.

### CLI
```bash
python tools/rag/retention_manager.py --detect-anomalies --json
```

### Config (`args/rag_config.yaml` → `rag.retention.anomaly_detection`)
```yaml
anomaly_detection:
  enabled: true
  min_samples: 20
  stddev_k: 2.0
  adaptive_bounds:
    hot_min: 30
    hot_max: 120
    warm_min: 365
    warm_max: 1460
```

## Guarantees
- `migrate_chunks` / `get_migration_candidates` / `get_retention_status`
  signatures and return shapes are **unchanged**; the anomaly surface is
  additive.
- Adaptive thresholds are clamped `>=` the configured tier age, so detection is
  never *more* aggressive than the static rule it augments.
- All DB access via `get_connection()`; connections closed in `finally`.

## Verification
- `tests/test_retention_manager.py` — 20 unit tests (constants, timestamp
  parsing, config loading, threshold computation, detection result shape,
  statistical correctness). All passing.
- `ruff check` clean on the module and tests.
- CLI smoke test (`--detect-anomalies --json`) returns a well-formed result.
