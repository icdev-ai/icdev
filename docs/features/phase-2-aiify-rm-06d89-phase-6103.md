# Phase 2 — aiify-rm-06d89-phase-6103 (dup of aiify-opp-6091)

**Roadmap:** `rm-06d89040cf` · **scan_id:** 43 · **opportunity_id:** 6103
**Pattern:** `keyword_list_search` → `embedding_search`
**External target:** `src/paperless/parsers/tesseract.py` (paperless-ngx clone `aiify_git_zwu66zfu`)

## Determination: duplicate — already implemented

The opportunity's `module_path` points at a temporary `aiify_git_*` shallow-clone of
the external paperless-ngx repo, which the AI-ify engine clones, scans, and deletes.
The clone is reaped and the file is external/unmodifiable. Per the established
disposition for external-repo opps, the AI-ification lands in the **analogous ICDEV
subsystem** — the Document Intelligence Canvas (DIC), `tools/document_intelligence/`.

The `keyword_list_search → embedding_search` paradigm was already implemented for DIC
as **aiify-opp-6091** (commit `b9ae780dc`): `DICSearchEngine.keyword_search()` +
`KeywordSearchResult` in `tools/document_intelligence/search_engine.py`. It upgrades a
literal keyword-list match to semantic embedding/vector retrieval (the keywords are
embedded as a single query and matched against chunk embeddings), with an honest
`embedding_used` flag and a graceful literal-keyword fallback when no embedding
provider is configured (air-gap), so results are never *worse* than the keyword-list
baseline.

## Verification (HEAD `b9ae780dc`, irad/feature)

- `b9ae780dc` is HEAD (and an ancestor of HEAD).
- `class KeywordSearchResult` with `embedding_used` field — present (search_engine.py:198, 215).
- `def _embeddings_available()` — present (search_engine.py:523).
- `def keyword_search()` — present (search_engine.py:538); selects `hybrid` (vector+rerank)
  when embeddings available, `grounded` literal fallback otherwise.
- `tests/test_dic_search_keyword.py` — 8/8 pass.

This tesseract.py opp is the same paradigm against a deleted external file; no new
internal target distinct from 6091. Closed as a duplicate with
`bypass_verification:true` + `bypass_reason`.
