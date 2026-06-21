# Phase 2 — Adaptive anomaly_detection thresholds for RAG reranker provider

<!-- CUI // SP-CTI -->

**Task:** `aiify-rm-6efad-phase-5513`
**Opportunity:** 5513 (scan 28, roadmap `rm-6efad73721`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**Module:** `tools/rag/reranker_provider.py`
**Model recommendation:** `claude-haiku-4-5-20251001`

## Summary

Extracted the hardcoded thresholds in `tools/rag/reranker_provider.py` to named,
config-overridable constants and added adaptive anomaly detection over reranker
output confidence.

### Extracted constants (module-level, override via `args/rag_config.yaml`)

| Constant | Default | Meaning |
|----------|---------|---------|
| `_DEFAULT_TOP_K` | 5 | Default number of ranked results returned |
| `_BGE_DOC_CHARS` | 800 | Per-document chars sent to the BGE cross-encoder |
| `_BGE_EMBED_TIMEOUT` | 30 | Seconds to wait for an Ollama `/api/embed` call |
| `_BGE_AVAIL_TIMEOUT` | 5 | Seconds to wait for an Ollama `/api/tags` probe |
| `_LLM_PREVIEW_CHARS` | 400 | Per-chunk preview chars sent to the LLM reranker |
| `_LLM_MAX_TOKENS` | 512 | Max tokens for the LLM-as-reranker call |
| `_LLM_TEMPERATURE` | 0.1 | Temperature for (near-)deterministic ranking |
| `_RERANK_SCORE_FLOOR` | 0.30 | Top relevance score below which a rerank is flagged |
| `_ANOMALY_STDDEV_K` | 2.0 | Flag runs below `mean − k·stddev` of history |

`BGERerankerProvider` and `LLMRerankerProvider` read their tunables from the
provided config dict (falling back to the module constants), so behavior is
changed in config, not code.

### Adaptive anomaly detection

- **`_compute_rerank_anomaly_thresholds()`** — derives a relevance floor as
  `mean − k·stddev` of historical `top_score` for reranked runs in
  `rag_retrieval_log` (`WHERE rerank_used = 1`). Population stddev is computed
  via `E[x²] − E[x²]` because SQLite has no `STDDEV()`. The floor is clamped by
  `adaptive_bounds.floor_min`/`floor_max` and falls back to the module floor
  when fewer than `min_samples` rows exist or detection is disabled.
- **`RerankerProvider.flag_anomaly(ranked)`** — flags a rerank result as a
  low-confidence ranking when its best document score falls below the (adaptive)
  floor, i.e. the reranker surfaced nothing strongly relevant. Returns
  `{"anomalous": bool, "reasons": [...], "score_floor": float}`; clean for an
  empty result set. Thresholds are configured lazily on first use.
- **`get_reranker_provider()`** calls `configure_anomaly_detection(cfg)` on the
  provider it returns, so the adaptive floor is computed once at construction.

### Config

New `reranker.anomaly_detection` block in `args/rag_config.yaml`:

```yaml
reranker:
  anomaly_detection:
    enabled: true
    min_samples: 30
    stddev_k: 2.0
    fallback_score_floor: 0.30
    adaptive_bounds:
      floor_min: 0.05
      floor_max: 0.80
    bge_doc_chars: 800
    bge_embed_timeout: 30
    bge_avail_timeout: 5
    llm_max_tokens: 512
    llm_temperature: 0.1
```

## Tests

`tests/test_reranker_provider.py` — 20 unit tests covering extracted constants,
config-driven provider overrides, config loading precedence, adaptive-threshold
fallback paths, `flag_anomaly` flagging logic (low/high/empty/exact-floor/lazy),
and factory wiring. All passing; `ruff` clean.

## Notes

The `rerank()` return signature (`List[Tuple[int, float]]`) is unchanged —
anomaly detection is exposed as a separate `flag_anomaly()` method so existing
callers are unaffected.
