# CUI // SP-CTI

# Phase 2 — AI-ify: Fulltext Search → LLM Generation for `paperless-ngx` `src/documents/permissions.py`

**Task:** `aiify-rm-06d89-phase-6047`
**Opportunity:** 6047 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `fulltext_search_engine` → **`llm_generation`**
**Phase:** P2 — Core Modernization
**Recommended model:** `claude-sonnet-4-6`
**Scores:** composite 0.6908 · value 0.698 · feasibility 0.845 · risk 0.595

> **Scope note — advisory, external repo.** Scan 43 targets the third-party
> open-source project **paperless-ngx** (`src/documents/permissions.py`), cloned
> into an ephemeral temp directory (`…/aiify_git_zwu66zfu/…`) that the scan engine
> has already removed. ICDEV does **not** own or vendor this code. Per the
> established `aiify-opp` pattern the augmentation lands in the **analogous ICDEV
> subsystem** — the Document Intelligence Canvas (DIC).

## Determination: DUPLICATE — already implemented under `aiify-opp-6045` (and `6046`)

Scan 43 flagged the **same** `fulltext_search_engine → llm_generation`
augmentation from the **same** paperless-ngx file (`src/documents/permissions.py`)
multiple times. The scanner emits one opportunity per (file, pattern) site, so
paperless' permission-filtered fulltext-search path keeps re-producing this
opportunity, distinct only by a re-numbered id:

| | 6044 | 6045 | 6046 | **6047** |
|---|---|---|---|---|
| pattern_type | `fulltext_search_engine` | `fulltext_search_engine` | `fulltext_search_engine` | `fulltext_search_engine` |
| ai_paradigm | `llm_generation` | `llm_generation` | `llm_generation` | `llm_generation` |
| scan / roadmap | 43 / `rm-06d89040cf` | 43 / `rm-06d89040cf` | 43 / `rm-06d89040cf` | 43 / `rm-06d89040cf` |
| flagged file | `src/documents/permissions.py` | `src/documents/permissions.py` | (sibling search-path file) | `src/documents/permissions.py` |
| disposition | dup → 6046 (`484435dc2`) | **shipped** (`79f5a0d4c`) | **shipped** (`970ad25a5`) | **dup → 6045** (this doc) |

This matches the known "duplicate AI-ify opportunities collide" pattern:
**verify and close as a duplicate; do not author a competing copy.**

6047's `module_path` is `src/documents/permissions.py` — the **access-control
layer** of paperless' fulltext search. That is precisely the angle already
shipped under **`aiify-opp-6045`**, whose commit message reads verbatim:
*"fulltext_search_engine -> llm_generation, modeled on the access-control layer of
a permission-aware fulltext search (paperless src/documents/permissions.py)."*

### Already shipped (ICDEV / DIC) — the permissions.py angle, commit `79f5a0d4c` (6045)

`tools/document_intelligence/search_engine.py` already realizes the
permission-aware `fulltext_search_engine → llm_generation` augmentation:

- **Clearance-aware retrieval** — `DICSearchEngine.search(..., clearance=…)` ranks
  each result's document classification via `_clearance_rank()` against
  `CLASSIFICATION_LEVELS` and drops anything above the caller's clearance **before**
  the `top_k` cap, so accessible results are never starved by withheld ones.
  Backward-compatible: `clearance=None` (default) applies no filtering.
- **Grounded, non-leaking access explanation** — `DICSearchEngine.access_explanation()`
  → `DICAccessExplanation` partitions matches by clearance and, when anything is
  withheld, composes a short natural-language notice of *what* was withheld and
  *why*. The LLM is fed **only** the per-level classification counts and the
  clearance — never document content, titles, or the raw query — so it physically
  cannot disclose protected material.
- **Deterministic fallback** — a leak-free template message is always produced (and
  used to ground the LLM); the access notice never fails or breaks search.
- **Safe-by-default ranking** — unknown / compound markings normalize to the
  nearest base tier (never silently to UNCLASSIFIED); any `TOP SECRET` variant
  collapses to the highest tier.

### Also shipped — grounded answer synthesis, commit `970ad25a5` (6046)

`DICSearchEngine.answer()` → `DICAnswer` composes a grounded, cited answer over
the NO-LLM fulltext results (BM25 + KG, with a BM25 air-gap fallback), emitting
`INSUFFICIENT_EVIDENCE` → `grounded=False` when the context cannot answer, with
`no_evidence` / `llm_unavailable` degradation paths and bounded cost.

DIC's fulltext search is further permission-aware at the storage layer via
RBAC+ABAC+RLS (`dic_team_access` + per-`collection_id` filtering).

### Verification

- `tests/test_dic_search_access.py` — **12 passed** (clearance ranking,
  withholding/breakdown, the non-leak invariant, LLM fallback, top_k filling).
- `tests/test_dic_search_answer.py` pins the grounded/refusal/degradation paths.

## Status

Closed as **duplicate of `aiify-opp-6045`** (with `6046`) — the
`fulltext_search_engine → llm_generation` paradigm for the access-control /
`permissions.py` angle is fully implemented and tested in DIC. No new ICDEV
runtime code is warranted for 6047; authoring a parallel copy would duplicate
`search_engine.py:search(clearance=…)` / `access_explanation()`. This doc records
the determination; **no runtime change**.
