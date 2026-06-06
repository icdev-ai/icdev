# Phase 2 — AI-ify `aiify-rm-06d89-phase-6091` (keyword_list_search → embedding_search)

**Status:** Implemented.
**Date:** 2026-06-05
**Roadmap:** `rm-06d89040cf` · **Scan:** 43 · **Opportunity:** 6091

## Opportunity

- **Pattern:** `keyword_list_search` → **AI paradigm:** `embedding_search`
- **Module (external):** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\views.py`
- **Model recommendation:** `claude-haiku-4-5-20251001`
- **Composite score:** 0.6499 (value 0.6535 / feasibility 0.8525 / risk 0.7125)

## Determination

The `module_path` points at a **temp `aiify_git_*` shallow clone** of the external
paperless-ngx repo (`src/documents/views.py`). The AI-ify engine clones, scans, then
deletes the clone, so the file is gone by the time this card runs (verified missing). Per
established disposition, the AI-ification is landed in the **analogous ICDEV internal
subsystem** — the **Document Intelligence Canvas (DIC)**. Pattern + paradigm decide the
internal analog, not the external filename.

For `keyword_list_search → embedding_search`, the internal analog is DIC's search layer,
`tools/document_intelligence/search_engine.py`. A classic keyword-list filter (paperless
`views.py`) matches a document only when it contains one of an exact list of keywords. The
embedding-search upgrade matches a document when it is *semantically* similar to the
keywords, even when it never uses the literal term.

## Implementation

- **`DICSearchEngine.keyword_search(keywords, collection_id, top_k, clearance)`** — new
  OPT-IN method. Normalizes the keyword list (trim / de-dup case-insensitively /
  order-preserve), embeds the keywords as a single query, and retrieves semantically
  similar cited chunks via the existing vector pipeline (`mode="hybrid"`).
- **Graceful degradation (air-gap safe):** `_embeddings_available()` checks for an
  embedding provider. When none exists, the method falls back to `mode="grounded"`, whose
  BM25 path is literal keyword matching — so results are **never worse than the keyword-list
  baseline**. `embedding_used` on the result records which path actually ran.
- **`DICKeywordSearchResult`** dataclass — `keywords`, `results` (cited `DICSearchResult`s),
  `embedding_used`, `result_count`, `refusal_reason` (`"no_keywords"` for an empty/blank
  list, run-free; `"no_matches"` when nothing matched), `origin="ai_retrieved"`, plus
  `to_dict()`.
- **Access control preserved:** results route through `DICSearchEngine.search`, so the
  caller's `clearance` filters out over-classified documents before the `top_k` cap and
  every result keeps its full citation pack.
- **Model rule:** retrieval uses the configured embedding provider via `LLMRouter`/
  `.env`-driven config; no model ID is hardcoded (honoring the card's `claude-haiku`
  intent without violating the no-hardcoded-model guardrail).
- **API wiring:** `POST /document-intelligence/api/search` accepts an opt-in `keywords`
  list; when present, the response includes a `keyword_search` block. Existing `query`,
  `expand`, and `explain_access` behavior is unchanged.

## Verification

- `tests/test_dic_search_keyword.py` — **8/8 pass** (normalization, run-free empty refusal,
  hybrid path on embeddings-present, grounded fallback on embeddings-absent, no-match
  refusal, clearance/collection threading, `to_dict` shape).
- Existing `tests/test_dic_search_answer.py` + `tests/test_dic_search_access.py` —
  **23/23 pass** (no regression).
- `ruff check` clean on `search_engine.py`, `blueprint.py`, and the new test.
- `search_engine` + `blueprint` import cleanly.
