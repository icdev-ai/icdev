# Phase 2 — Adaptive anomaly_detection thresholds for RAG SQLite vector store

<!-- CUI // SP-CTI -->

**Task:** `aiify-rm-6efad-phase-5523`
**Opportunity:** 5523 (scan 28, roadmap `rm-6efad73721`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**Module:** `tools/rag/sqlite_vector_store.py`
**Model recommendation:** `claude-haiku-4-5-20251001`

## Summary

Extracted the hardcoded thresholds in `tools/rag/sqlite_vector_store.py` to
named, config-overridable constants and added adaptive anomaly detection over
vector-search retrieval confidence.

### Extracted constants (module-level, override via `args/rag_config.yaml`)

| Constant | Default | Meaning |
|----------|---------|---------|
| `_DEFAULT_TOP_K` | 50 | Default number of nearest-neighbour results returned by `search()` |
| `_BUSY_TIMEOUT_MS` | 5000 | SQLite `busy_timeout` (ms) for WAL contention |
| `_VECTOR_SCORE_FLOOR` | 0.30 | Top similarity score below which a search is flagged |
| `_ANOMALY_STDDEV_K` | 2.0 | Flag searches below `mean − k·stddev` of history |

`SQLiteVectorStore.__init__` accepts an optional `config` dict and reads
`busy_timeout_ms` from it (falling back to the module constant), so the
connection tunable is changed in config, not code.

### Adaptive anomaly detection

- **`_compute_vector_anomaly_thresholds()`** — derives a relevance floor as
  `mean − k·stddev` of historical `top_score` for vector searches in
  `rag_retrieval_log` (`WHERE retrieval_mode = 'vector'`). Population stddev is
  computed via `E[x²] − E[x]²` because SQLite has no `STDDEV()`. The floor is
  clamped by `adaptive_bounds.floor_min`/`floor_max` and falls back to the
  module floor when fewer than `min_samples` rows exist or detection is
  disabled.
- **`SQLiteVectorStore.flag_anomaly(results)`** — flags a vector search as a
  low-confidence retrieval when its best (top-1) cosine similarity falls below
  the (adaptive) floor, i.e. nothing in the corpus is strongly similar to the
  query. Returns `{"anomalous": bool, "reasons": [...], "score_floor": float}`;
  clean for an empty result set. Thresholds are configured lazily on first use.
- **`SQLiteVectorStore.configure_anomaly_detection(config)`** — loads the
  anomaly config and computes the adaptive floor once, so callers can warm the
  threshold explicitly.

### Config

New `sqlite_vector_store.anomaly_detection` block in `args/rag_config.yaml`:

```yaml
sqlite_vector_store:
  anomaly_detection:
    enabled: true
    min_samples: 30
    stddev_k: 2.0
    fallback_score_floor: 0.30
    adaptive_bounds:
      floor_min: 0.05
      floor_max: 0.80
    busy_timeout_ms: 5000
```

## Tests

`tests/test_sqlite_vector_store_anomaly.py` — 16 unit tests covering extracted
constants, config-driven `busy_timeout` override, config loading precedence,
adaptive-threshold fallback paths, and `flag_anomaly` flagging logic
(low/high/empty/exact-floor/lazy). All passing; `ruff` clean.

## Notes

The `search()` return signature (`List[SearchResult]`) is unchanged — anomaly
detection is exposed as a separate `flag_anomaly()` method so existing callers
(`tools/rag/retrieval_orchestrator.py` and friends) are unaffected.
