# Phase 2 — aiify-rm-06d89-phase-6122 (Determination: duplicate of aiify-opp-6052)

- **Roadmap:** `rm-06d89040cf`
- **Scan ID:** 43
- **Opportunity ID:** 6122
- **Pattern:** `hardcoded_threshold` → `anomaly_detection`
- **External module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\paperless_ai\chat.py`
- **Disposition:** Closed as **duplicate** of already-shipped **aiify-opp-6052** — no new code.

## Why this is a duplicate

The `module_path` points at a temp `aiify_git_*` clone of an **external open-source
repo** (paperless-ngx / paperless-ai). The AI-ify engine shallow-clones, scans, then
deletes that tree, so the file is gone and unmodifiable by the time this card runs.
Per the established disposition, the AI-ification lands in the **analogous ICDEV
internal subsystem** and external-repo siblings that collapse onto an already-built
analog are closed as duplicates.

`src/paperless_ai/chat.py` is a **RAG chat over documents**: it retrieves document
chunks by relevance/similarity score and applies a hardcoded score cutoff to decide
which retrieved context is "good enough" to feed the model. That hardcoded relevance
cutoff is the exact `hardcoded_threshold` the scanner flags, and `anomaly_detection`
is the recommended paradigm. This is the **same retrieval-relevance pattern** already
addressed by aiify-opp-6052 in the ICDEV analog:
**`tools/document_intelligence/search_engine.py`** (the DIC RAG retrieval/search layer).

This external file is also a long-running re-emitter: the identical
`chat.py` `hardcoded_threshold → anomaly_detection` opportunity has recurred across
many scans (e.g. 1853–1855, 3770–3772, 4195–4197, 4617–4619, 4779–4781, 4978–4980,
5161–5163, and now 6122–6124). It maps to one ICDEV capability, already built.

That analog was implemented under **aiify-opp-6052** (commit `422f7adf5`,
ancestor of HEAD). It lifts a single hardcoded relevance cutoff into:

- Named absolute relevance bands (`_RELEVANCE_STRONG` = 0.60 / `_RELEVANCE_WEAK` =
  0.30, `_classify_relevance`), replacing the magic "min score" constant — the same
  cutoff a RAG chat would hardcode to gate retrieved context.
- A result-set-relative statistical outlier pass (`_compute_search_anomalies`):
  flags the noise tail past a relevance cliff (score below `mean − k·stdev`,
  k = `_ANOMALY_STDEV_K` = 1.5) and a `low_confidence` query (even the top hit is
  weak — i.e. the chat retrieved no good context at all). Pure stdlib, air-gap safe,
  **always authoritative**.
- An optional best-effort LLM severity grade (`_ai_search_anomaly_severity`, router
  fn `dic_search_anomaly_severity`) that degrades silently to the deterministic
  baseline when unavailable — never a hard dependency.
- Public entry points `detect_search_anomalies(query, results, use_llm=...)` and
  `DICSearchEngine.search_with_quality(...)`, which annotate retrieval quality
  **without changing which results `search()` returns**.

This is precisely the relevance-cliff / low-confidence anomaly detection that the
retrieval step of a RAG chat needs. There is no distinct, non-duplicative target for
`chat.py` — authoring a competing copy would collide with the shipped 6052
implementation. Phase `6054` (the query-side sibling `_query.py`) was already closed
as a duplicate of 6052 on the same basis (commit `f5c7dbe25`).

## Verification at HEAD

- `aiify-opp-6052` impl commit `422f7adf5` is an ancestor of HEAD.
- `tools/document_intelligence/search_engine.py` (committed): contains
  `_compute_search_anomalies`, `detect_search_anomalies`, `search_with_quality`,
  `_ai_search_anomaly_severity`, `_classify_relevance`, and the `aiify-opp-6052`
  provenance comment. Live import confirmed: bands 0.30 / 0.60,
  `_classify_relevance(0.7)="strong"`, `_classify_relevance(0.1)="weak"`.
- `tests/test_dic_search_anomaly.py` ships with the 6052 build.
- Working tree clean — no code change required for this card.

## Outcome

Card moved to **done**. No source change — this is a determination that the
opportunity is a duplicate of already-shipped `aiify-opp-6052` (commit `422f7adf5`).
