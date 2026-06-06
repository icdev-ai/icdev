# CUI // SP-CTI

# AI-ify opp-6039 — DIC Query Expansion (`fulltext_search_engine -> llm_generation`)

## Origin
- **Opportunity:** 6039 (scan 43, roadmap `rm-06d89040cf`, Phase 2 — Core Modernization)
- **Source pattern:** `fulltext_search_engine` in an external repo's `src/documents/matching.py`
  (paperless-style document-matching module). The AI-ify engine clones that repo
  into a temp dir and deletes it after scanning, so the change is applied to the
  **analogous ICDEV subsystem** — the Document Intelligence Canvas (DIC) grounded
  search engine — rather than the throwaway external file.
- **Model recommendation:** `claude-sonnet-4-6` (routed via `summarization`).

This is the third LLM layer to land on `tools/document_intelligence/search_engine.py`
for this pattern, joining opp-6046 (`answer` synthesis) and opp-6045
(`access_explanation`). It is a **distinct** capability — it improves *matching*
(query→document recall), not answering or access notices.

## What shipped
`DICSearchEngine.expand_query(query, max_terms=8)` — an **opt-in** layer that asks
the LLM for additional search keywords / synonyms so a document phrased
differently than the query still matches. Returns a `DICQueryExpansion`:

| field | meaning |
|-------|---------|
| `original_query` | the caller's query (stripped) |
| `terms` | extra keywords, de-duplicated vs the query's own words, capped at `_EXPANSION_MAX_TERMS` (8) |
| `expanded_query` | `original_query` + appended terms (always usable for search) |
| `llm_used` | whether the model produced the terms |
| `refusal_reason` | `empty_query` / `llm_unavailable` / `no_terms` when no expansion applied |
| `origin` | `ai_generated` |

### Guarantees (pinned by tests in `tests/test_dic_search_expand.py`)
- **Never worse than baseline.** Empty query, LLM failure, blank/`None` output,
  the model's `NONE` sentinel, and no-usable-terms all degrade to the original
  query with a `refusal_reason` — `expanded_query` is always safe to search with.
- **Strictly additive & bounded.** Terms already in the query are dropped; the
  list is capped at 8; overlong "terms" (a returned sentence) are rejected via
  `_EXPANSION_MAX_TERM_LEN` (40).
- **Keyword-only, no fabrication.** The system prompt forbids answering the
  question or inventing proper nouns / numbers / facts — it emits general
  synonyms only.
- **Injection scanning stays ON** — the query is user-provided (no
  `skip_injection_scan`).
- **Air-gap safe** — when the LLM is unavailable, search proceeds on the
  original query exactly as before.

## Integration
`POST /document-intelligence/api/search` accepts an opt-in `"expand": true` flag.
When set, the route expands the query first, searches on `expanded_query`
(still clearance-enforced), and returns the `DICQueryExpansion` under
`payload["expansion"]`. Default behavior (flag absent) is unchanged.

## Tests
- `tests/test_dic_search_expand.py` — 12 tests, all passing.
- Regression: `tests/test_dic_search_answer.py` (13) + `tests/test_dic_search_access.py` (10) still pass.
- `ruff check` clean on all changed files.
