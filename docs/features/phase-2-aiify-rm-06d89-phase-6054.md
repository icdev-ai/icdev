# Phase 2 — aiify-rm-06d89-phase-6054 (Determination: duplicate of aiify-opp-6052)

- **Roadmap:** `rm-06d89040cf`
- **Scan ID:** 43
- **Opportunity ID:** 6054
- **Pattern:** `hardcoded_threshold` → `anomaly_detection`
- **External module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\search\_query.py`
- **Disposition:** Closed as **duplicate** of already-shipped **aiify-opp-6052** — no new code.

## Why this is a duplicate

The `module_path` points at a temp `aiify_git_*` clone of an **external open-source
repo** (paperless-ngx). The AI-ify engine shallow-clones, scans, then deletes that
tree, so the file is gone and unmodifiable by the time this card runs. Per the
established disposition, the AI-ification lands in the **analogous ICDEV internal
subsystem** and external-repo siblings that collapse onto an already-built analog
are closed as duplicates.

`src/documents/search/_query.py` is the **query side of the same fulltext-search
subsystem** as `src/documents/search/_backend.py` (the ranking backend). Both share
the identical pattern (`hardcoded_threshold`) and paradigm (`anomaly_detection`),
and both map to the same ICDEV analog: **`tools/document_intelligence/search_engine.py`**.

That analog was already implemented under **aiify-opp-6052** (commit
`422f7adf5`, on `irad/feature`, ancestor of HEAD). It lifts a search backend's
single hardcoded relevance cutoff into:

- Named absolute relevance bands (`_RELEVANCE_STRONG` / `_RELEVANCE_WEAK`,
  `_classify_relevance`), replacing the magic "min score" constant.
- A result-set-relative statistical outlier pass (`_compute_search_anomalies`):
  flags the noise tail past a relevance cliff (score below
  `mean − k·stdev`, k = `_ANOMALY_STDEV_K`) and a `low_confidence` query (even the
  top hit is weak). Pure stdlib, air-gap safe, **always authoritative**.
- An optional best-effort LLM severity grade (`_ai_search_anomaly_severity`,
  router fn `dic_search_anomaly_severity`) that degrades silently to the
  deterministic baseline when unavailable — never a hard dependency.
- Public entry points `detect_search_anomalies(query, results, use_llm=...)` and
  `DICSearchEngine.search_with_quality(...)`, which annotate quality **without
  changing which results `search()` returns**.

This is precisely the relevance-cliff / low-confidence anomaly detection that the
query side of a fulltext search needs. There is no distinct, non-duplicative target
for `_query.py` — authoring a competing copy would collide with the shipped 6052
implementation.

## Verification at HEAD

- Branch `irad/feature`; `aiify-opp-6052` impl commit `422f7adf5` is an ancestor of HEAD.
- `tools/document_intelligence/search_engine.py` (committed): contains
  `_compute_search_anomalies`, `detect_search_anomalies`, `search_with_quality`,
  `_ai_search_anomaly_severity`, and the `aiify-opp-6052` provenance comment.
- `tests/test_dic_search_anomaly.py`: **34 passed** (`pytest -q --noconftest`).
- Working tree clean — no code change required for this card.

## Outcome

Card moved to **done** with `bypass_verification: true` and a `bypass_reason`
naming this determination and commit `422f7adf5`. No source change.
