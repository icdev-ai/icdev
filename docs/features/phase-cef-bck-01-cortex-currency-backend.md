# CUI // SP-CTI

# Cortex `currency` Backend — "is this entity still current?" through the governed seam (cef-bck-01)

## The finding

`cef-fnd-04` built the substrate: `entity_currency`, one row per **(source,
entity, version)** assertion, aggregating the curated catalog
(`docmod_catalog_entries`, authoritative), `docmod_eol_products` and
`mc_net_eol_data` under a declared authority policy. Measured on the live
database, it holds **230 assertions**.

Nothing could ask it through Cortex. A canvas that wanted to know whether the
hardware in a document was still supported had to import the store directly,
which means: no routing, no per-backend timeout, no citation contract, no
governance gates, no `.errors` annotation — and a different answer shape per
caller. Search had a governed seam; currency did not.

## What shipped

A fifth Cortex retrieval backend, registered at the same five points the
existing four use.

| Point | Where |
|-------|-------|
| 1. adapter | `search_currency(query, top_k, ctx)` in `tools/cortex/search_service.py` |
| 2. constant | `CORTEX_BACKENDS` in `tools/cortex/schemas.py` |
| 3. dispatch | `BACKEND_ADAPTERS` in `tools/cortex/search_service.py` |
| 4. routing | `ROUTE_LABEL_BACKENDS["currency"]` + `_CURRENCY_PATTERNS` |
| 5. config | `search.strategy_weights.currency: 0.7`, `search.timeouts.currency: 5.0`, `search.fan_out.backends` in `args/cortex_config.yaml` |

### Two lanes, kept apart on purpose

**ASSERTION** — `entity_currency.search()`, a new free-text read that groups
matching rows per entity and resolves each group through the store's OWN policy
(`_sort_by_policy`, `_is_authoritative`), so the authority order is not
re-implemented in Cortex.

**LEARNER** — `defacto_learner.search()` over `docmod_defacto_standards`, what
the inventory feeds learned is actually fielded. Corroboration and tie-breaker,
never authority. Each hit carries its `source_feed` and `evidence_kind`, because
a modelled design and an observed estate are different claims.

### The authority order is STRUCTURAL, not emergent

Scores are **banded**: curated `[0.75, 1.00]`, external feed `[0.45, 0.75]`,
learner `[0.10, 0.45]`. A curated catalog assertion cannot rank below an EOL
feed's however confident the feed is, and no learner row can reach either. This
is the rule `args/entity_currency.yaml` already states for read-time resolution
— *"a tie-break that a bumped prior can overturn is not authority"* — applied to
the ranking a caller actually reads. Inside a band, quality is
`0.5 * term_match + 0.5 * confidence` (or `share_pct` for the learner lane); the
declared prior is half the quality and never the whole of it, because it is a
per-source constant that would otherwise rank every row from one source
identically regardless of what was asked.

Native scores are preserved verbatim in `raw_scores` (`confidence`, `share_pct`,
`weighted_score`, `deploy_count`, `match`, `band`); only the normalized `score`
is clamped.

### A dead table is a FAILURE, never an empty success

Each lane is exception-isolated **separately** and neither can raise. A dead
`entity_currency` returns `BackendResults([], errors=[{backend, stage: "store",
message}])`; a dead `docmod_defacto_standards` still returns the store's hits
with `stage: "corroboration"` on `.errors`. An empty result with EMPTY errors
means the corpus genuinely matched nothing.

That distinction only survives because the two new reads **raise**.
`entity_currency.query()` logs and returns `[]` on a DB failure — correct for a
point lookup, fatal here: a swallowing read makes a dead table byte-identical to
an entity nobody has heard of, which is exactly the defect `ctx-perf-04` added
the annotation for. `search()` therefore propagates, and a test pins it.

This is not hypothetical. `docmod_defacto_standards` holds **0 rows today** —
`defacto_learner.recompute()` runs nightly and its input `ni_devices` is empty
(cef-fnd-04). The learner lane is live, present and empty, and the adapter must
report that as *matched nothing*, not as *broken*. `coherence_checker
--check substrate_liveness` warns about the same empty substrate; the warning is
correct and the adapter is built for it.

### Routing

`_CURRENCY_PATTERNS` (EOL/EOS, deprecated, superseded, retired, sunset, "still
supported", "out of support", tech refresh) routes a lifecycle question to the
`currency` backend alone. It is checked **after** the three existing pattern
rules, so no query that routes somewhere today changes route — this rule only
claims questions nothing else was claiming. `currency` also joins the ambiguous
fan-out set: two indexed `LIKE` reads, no embedding call and no model call, at a
5s timeout rather than the 10s default.

## Verified live

```
$ python -c "... search_currency('is TLS 1.1 still current?')"
0.931 curated | docmod_catalog_entries ec-846ca0e8... | (?i)\bTLS[ ]?v?1\.1\b (protocol) — deprecated per docmod_catalog_entries (as of 2026-07-10).

$ python -c "... search('Catalyst 6500 end-of-life', strategy='currency')"
0.713 currency:override[currency] | mc_net_eol_data | Catalyst 6500 (hardware_model) — end_of_life per mc_net_eol_data (as of 2026-08-16). End of life: 2018-07-31.
```

Router label for both queries: `currency`. `.errors`: empty.

## Tests

`tests/cortex/test_search_currency_backend.py` (gated in this PR, per the
test-gating policy): registration, the config declarations, a cited result for a
known-deprecated entity, score clamping with the native value preserved, the
curated > feed > learner ordering, evidence-class reporting on a learner hit,
conflict surfaced in the content, and the four error cases — dead store, dead
learner with partial results, empty corpus with empty errors, and never raising.
