# CUI // SP-CTI

# Phase 2 — AI-ify: Search-Relevance Anomaly Detection for `paperless-ngx` `src/documents/search/_backend.py`

**Task:** `aiify-rm-06d89-phase-6052`
**Opportunity:** 6052 (scan 43, roadmap `rm-06d89040cf`)
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

The scanner flagged `src/documents/search/_backend.py` — the result-ranking
backend of a fulltext search engine. A classic search backend keeps a single
**hardcoded relevance cutoff** (`hardcoded_threshold`): show any hit above
score *X*, drop the rest. That fixed number is blind to two failure modes it
structurally cannot see:

1. **Low-confidence retrieval** — a query whose *best* hit is itself weak (the
   whole result set is noise; the query found nothing good).
2. **Relevance cliff** — a result set that is strong up to a point and then
   drops off a statistical edge into a noise tail.

The recommended paradigm, `anomaly_detection`, fits both: instead of one fixed
cutoff, look at the *distribution* of a query's result scores and flag what is
anomalous **for that query**.

## Recommended Augmentation

**Paradigm:** `anomaly_detection` — lift the implicit cutoff into named relevance
bands, then add a pure, offline score-distribution outlier pass over the result
set, with an optional LLM layer grading only *severity*.
**Model:** `claude-haiku-4-5-20251001` — cheap, bounded severity judgement for a
P2 enrichment; never on the critical path.

## Status — Implemented (ICDEV / DIC)

Per the established `aiify-opp` pattern (ephemeral scan repo → land in the
analogous ICDEV subsystem), the capability was implemented in the **DIC Grounded
Search Engine**, mirroring the freshness-anomaly sibling `aiify-opp-6042` and the
date-parsing sibling `aiify-opp-6048`.

### `tools/document_intelligence/search_engine.py`

New, additive code. It **never changes which results `search()` returns** — it
only annotates result-set quality.

- **Named relevance bands** — `_RELEVANCE_STRONG` (0.60) / `_RELEVANCE_WEAK`
  (0.30) replace the implicit hardcoded "min score" cutoff. `_classify_relevance`
  is a pure function mapping a score to `strong` / `moderate` / `weak`.
- **`_compute_search_anomalies(results)`** — the **always-authoritative**,
  LLM-free, air-gap-safe statistical pass:
  - **Low outlier** — a result whose score is below `mean - _ANOMALY_STDEV_K *
    stdev` (k = 1.5) AND not itself strong (`< _ANOMALY_ABS_CEIL`) — the noise
    tail past a relevance cliff.
  - **`low_confidence`** — True when even the top result's score is below
    `_RELEVANCE_WEAK` (the query found no good match at all).
  - Guarded for tiny sets: below `_ANOMALY_MIN_RESULTS` (4) no outliers are
    reported, but `low_confidence` is still evaluated.
- **`_heuristic_search_anomaly_severity(weak, total, low_confidence)`** — pure
  deterministic baseline (`low` / `medium` / `high`) on the weak fraction,
  escalated to `high` whenever the retrieval is low-confidence.
- **`detect_search_anomalies(query, results, use_llm=True)`** — orchestrates the
  pure pass and layers the optional LLM grade on top; returns a public report
  (`anomaly_count`, `mean`, `stdev`, `threshold`, `top_score`, `low_confidence`,
  `weak_count`, `anomalies[]`, `severity`, `ai_grade`).
- **`_ai_search_anomaly_severity(query, summary, anomalies)`** — best-effort LLM
  severity grade routed via the dedicated `dic_search_anomaly_severity` key.
  Grounded on the real distribution + a bounded sample (`_ANOMALY_SAMPLE` = 5) of
  outliers. **Injection scanning stays ON** (the query is user-provided —
  `skip_injection_scan` is not set). Degrades silently to `None` on no-data,
  blank/malformed/out-of-range output, or any LLM failure — callers fall back to
  the deterministic baseline.
- **`DICSearchEngine.search_with_quality(...)`** — thin convenience that runs the
  identical `search()` and returns `(results, report)`.

### `args/llm_config.yaml`

Registered `dic_search_anomaly_severity` → chain `[claude-haiku, qwen3-local,
gpt-4o-mini, llama-local]`, effort `low`. Best-effort; the statistical detector
is authoritative.

## Why the deterministic layer is authoritative

DIC search is **air-gap safe** by default (BM25 + KG, no LLM). The anomaly
detection preserves that guarantee: every flag is produced by pure statistics
over the returned scores. The LLM only *grades severity* of already-detected
anomalies, and its absence never changes a flag. Quality assessment is purely
advisory and never alters retrieval or access control.

## Tests

`tests/test_dic_search_anomaly.py` — **34 tests, all passing**:

- band-constant ordering + `_classify_relevance` (strong/moderate/weak);
- pure detection: min-results guard, relevance-cliff outlier, never-flag-strong,
  low-confidence on/off, empty/uniform sets, outlier sort order;
- deterministic severity baseline incl. low-confidence escalation;
- LLM enrichment: routing key, skip-when-clean, runs-on-low-confidence,
  injection-scan-on, query grounding, bounded sample, fenced JSON, out-of-range /
  malformed / blank / failure → `None`, bounded rationale/concern;
- `detect_search_anomalies` orchestration + report shape.

Existing DIC search tests (`test_dic_search_*`) remain green (145 passed).

## Verification

```bash
python -m pytest tests/test_dic_search_anomaly.py -q          # 34 passed
python -m pytest tests/ -k "dic_search or search_engine" -q   # 145 passed
ruff check tools/document_intelligence/search_engine.py tests/test_dic_search_anomaly.py
```

## Files

- `tools/document_intelligence/search_engine.py` — bands + anomaly detection + LLM grade + `search_with_quality`
- `args/llm_config.yaml` — `dic_search_anomaly_severity` registration
- `tests/test_dic_search_anomaly.py` — 34 tests
- `tools/manifest/document-intelligence-canvas.md` — manifest entry updated
- `docs/features/phase-2-aiify-rm-06d89-phase-6052.md` — this doc
