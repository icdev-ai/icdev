# CUI // SP-CTI

# Phase 2 — AI-ify: Fulltext Search → LLM Generation for `paperless-ngx` `src/documents/permissions.py`

**Task:** `aiify-rm-06d89-phase-6044`
**Opportunity:** 6044 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `fulltext_search_engine` → **`llm_generation`**
**Phase:** P2 — Core Modernization
**Recommended model:** `claude-sonnet-4-6`
**Scores:** composite 0.6913 · value 0.699 · feasibility 0.845 · risk 0.595

> **Scope note — advisory, external repo.** Scan 43 targets the third-party
> open-source project **paperless-ngx** (`src/documents/permissions.py`), cloned
> into an ephemeral temp directory (`…/aiify_git_zwu66zfu/…`) that the scan engine
> has already removed. ICDEV does **not** own or vendor this code. Per the
> established `aiify-opp` pattern the augmentation lands in the **analogous ICDEV
> subsystem** — the Document Intelligence Canvas (DIC).

## Determination: DUPLICATE — already implemented under `aiify-opp-6046`

Opportunity **6044** and opportunity **6046** are the *same* augmentation flagged
twice from sibling files in the same ephemeral paperless-ngx checkout:

| | 6044 | 6046 |
|---|---|---|
| pattern_type | `fulltext_search_engine` | `fulltext_search_engine` |
| ai_paradigm | `llm_generation` | `llm_generation` |
| scan / roadmap | 43 / `rm-06d89040cf` | 43 / `rm-06d89040cf` |
| flagged file | `src/documents/permissions.py` | (sibling search-path file) |

The scanner emits one opportunity per (file, pattern) site, so paperless'
permission-filtered fulltext-search path produced a second
`fulltext_search_engine → llm_generation` opportunity distinct only by source
file. The recommended ICDEV augmentation — composing a grounded natural-language
answer over cited fulltext-search results — is identical. This matches the known
"duplicate AI-ify opportunities collide" pattern: **verify and close as a
duplicate; do not author a competing copy.**

### Already shipped (ICDEV / DIC), commit `970ad25a5`

`tools/document_intelligence/search_engine.py` already realizes
`fulltext_search_engine → llm_generation` via `DICSearchEngine.answer()` →
`DICAnswer`:

- **Grounded synthesis** — runs the normal NO-LLM cited search (BM25 + KG, with a
  BM25 air-gap fallback), then asks the LLM (`summarization` function,
  `claude-sonnet-4-6`) to compose an answer using **only** the retrieved excerpts,
  with inline `[n]` citation markers.
- **No fabrication** — strict system prompt; emits the `INSUFFICIENT_EVIDENCE`
  sentinel → `grounded=False, refusal_reason="insufficient_evidence"` when the
  context cannot answer.
- **Graceful degradation** — `no_evidence` (empty results) and `llm_unavailable`
  (provider/timeout/empty response) refusal paths; search never breaks.
- **Bounded cost** — top-`_ANSWER_MAX_RESULTS` (6) results, `_ANSWER_CHARS_PER_RESULT`
  (800) excerpt budget per source.
- **Injection scanning stays ON** — the query is user-provided.

### The `permissions.py` (access-control) angle is also covered

The 6044 source file is paperless' **permission filtering** on search. DIC's
fulltext search is already permission-aware: RBAC+ABAC+RLS via `dic_team_access`
and per-`collection_id` filtering inside `DICSearchEngine.search()` (committed
under `dic-search-01`). No additional access-control work is required.

### Verification

- `tests/test_dic_search_answer.py` pins the grounded/refusal/degradation paths.
- `DICSearchEngine.answer()` import + presence sanity-checked.

## Status

Closed as **duplicate of `aiify-opp-6046`** — the
`fulltext_search_engine → llm_generation` paradigm is fully implemented in DIC
(grounded, cited, HITL-safe answer synthesis with permission-aware retrieval). No
new ICDEV runtime code is warranted for 6044; authoring a parallel copy would
duplicate `search_engine.py:answer()`.
