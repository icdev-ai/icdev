# CUI // SP-CTI

# Phase 2 — AI-ify: Anomaly Detection for `tools/rag/evaluator.py`

**Task:** `aiify-rm-6efad-phase-5480`
**Opportunity:** 5480 (scan 28, roadmap `rm-6efad73721`)
**Pattern:** `hardcoded_threshold` → **`anomaly_detection`**
**Phase:** Phase 2 — Core Modernization

## Problem

`tools/rag/evaluator.py` (RAGAS-style RAG evaluator, D-RAG-22) carried frozen
magic numbers — the NDCG@k cutoff, LLM-as-judge token/temperature/truncation
limits, and score rounding precision — and had no mechanism to flag a retrieval
run whose quality regressed versus prior runs. It was the last RAG file not yet
covered by the `aiify-rag` / `aiify-rag-subsystem` sweeps (`crag_evaluator`,
`pdf_provider`, `chunker`, `retriever`, `reranker`, `faiss_vector_store`,
`codebase_indexer`).

## Change

Follows the canonical RAG aiify pattern (see `crag_evaluator._compute_crag_anomaly_thresholds`):

1. **Extracted constants** — module-level named constants, all overridable from
   `args/rag_config.yaml` under `evaluator.anomaly_detection`:
   `_DEFAULT_K`, `_LLM_MAX_TOKENS`, `_LLM_TEMPERATURE`, `_LLM_CHUNK_CHARS`,
   `_LLM_CONTEXT_CHARS`, `_LLM_ANSWER_CHARS`, `_SCORE_PRECISION`,
   plus anomaly floors `_NDCG_ANOMALY_FLOOR`, `_MRR_ANOMALY_FLOOR`,
   `_ANOMALY_STDDEV_K`.
2. **Adaptive anomaly detection** — `_compute_eval_anomaly_thresholds()` reads
   historical `ragas_ndcg` / `ragas_mrr` from `rag_evaluation_results` and derives
   a statistical lower bound `mean − k·stddev` per metric (population stddev via
   `E[x²] − E[x]²`, since SQLite has no `STDDEV()`), bounded by
   `adaptive_bounds.floor_min/floor_max`. Falls back to the module floors when
   `< min_samples` rows exist or detection is disabled.
3. **`RAGEvaluator.flag_anomalies()`** — flags an eval result as a quality
   regression when NDCG@k / MRR fall below the (adaptive) floor. Surfaced as an
   `anomaly` key in `evaluate_retrieval()` output and as an `anomaly_detection`
   block (thresholds + anomalous-case count) in `run_benchmark()` aggregates.

## Config

`args/rag_config.yaml` → `evaluator.anomaly_detection` (enabled, min_samples 30,
stddev_k 2.0, fallback floors 0.30, adaptive bounds 0.05–0.80, plus the
LLM-judge constants).

## Tests

`tests/test_rag_evaluator.py` — 16 unit tests (constants, adaptive-threshold
fallback paths, `flag_anomalies` boundary behavior, and `evaluate_retrieval`
anomaly surfacing + precision rounding). All passing. `ruff check` clean.

## Acceptance

- No behavior change to scores when history is sparse (floors == prior implicit
  thresholds; metrics rounded to the same 4 dp).
- Low-quality retrieval (relevant chunk absent) now surfaces an explicit anomaly.
- Thresholds tunable from config without editing code.
