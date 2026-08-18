# CUI // SP-CTI

# cef-di-04 — DIC grounded search asks ONE governed seam for its candidates

**Status:** shipped, toggle OFF
**Surface migrated:** `tools/document_intelligence/search_engine.py::DICSearchEngine._rag_search`
**Toggle:** `cortex.enabled` in `args/dic_search_config.yaml` (DEFAULT `false`)
**Seam:** `tools/document_intelligence/search_evidence.py` (+ `icdev/` mirror)

---

## What was there

`DICSearchEngine.search()` is the DIC canvas's retrieval surface. The dashboard
search page (`/api/dic/search`), `dic_chat`, `doc_generator`,
`output_generators` and ACE's DIC context injection all reach the corpus through
it — and so does Cortex itself, whose `dic` backend *is* this method.

Its candidate half was `_rag_search`: one `RAGRetriever.search(...)` call. Exactly
one rung. The currency store, the knowledge graph and the KB hold evidence about
the same entities and none of them were ever asked, because asking would have
meant the search engine learning four more table names.

## What it is now

`_rag_search` consults `search_evidence.resolve_evidence(query, ...)` first, which
makes ONE call — `cortex.resolve(query)` — fanning out over `currency`, `rag`,
`dic`, `graph` and `kb` under the 8-gate TRUST chain, writing a `cortex_audit`
row and registering a `source_citation_registry` row for the evidence set.

**Only *where candidates come from* moved.** Everything `search()` does *with* a
candidate is untouched and still runs, in the same order, on both paths:

1. `_chunk_meta` / `_doc_meta` enrichment into the mandatory `Citation` pack
2. the collection post-filter
3. **the clearance drop — still strictly before the `top_k` cap**
4. `_rerank_by_attribution` over the full accessible pool
5. the `top_k` cap

That ordering is preserved *by not moving it*. The seam returns candidates in the
shape `_rag_search` already returned (`chunk_id` / `content` / `source_id` /
`final_score`) and hands them to the same loop. A migration that re-implemented
the clearance drop would have to be re-proved; one that never touches it cannot
regress it.

The **BM25 air-gap fallback** is likewise untouched and is still the floor under
*both* paths: the seam declining, Cortex being absent, or the governed resolution
coming back empty all land on `_rag_search`'s direct retriever, which falls to
`_bm25_fallback` exactly as before.

`cortex.resolve` makes no model call of its own (it passes `corrective=False`, so
even the CRAG rewrite does not run), so turning the toggle on does not put an LLM
anywhere one was not already.

---

## The layering, resolved

Cortex's own `dic` rung **is** `DICSearchEngine.search()`
(`tools/cortex/search_service.py::search_dic`), and `dic` is in
`resolve.backends` in `args/cortex_config.yaml`. So

```
search() -> resolve() -> dic rung -> search() -> resolve() -> ...
```

is a real cycle, and it recurses inside a **bounded** `ThreadPoolExecutor`
(`search_service._get_search_executor`), which makes the failure mode pool
exhaustion rather than a slow query.

### Why the interlock is process-wide, not thread-local

`_run_backends` *submits* each backend onto the shared pool, so the re-entrant
call arrives on a **different thread**. The thread-local guard cef-di-01 and
cef-di-03 correctly use — nothing calls those two surfaces back, so their
re-entrancy is same-thread, inside `resolver.assess` — is structurally blind to a
pool hop. A thread-local guard here would look right, pass a single-threaded
test, and recurse in production.

The depth counter is therefore published on a module both copies of the seam
already import (`tools.rag.vector_store_provider`), so a recursion that enters
through `tools.` and returns through `icdev.tools.` is still seen.

**The rule it states:** *the innermost DIC search inside a resolve fan-out is
always the raw rung.* DIC asks Cortex; Cortex asks DIC; DIC does not ask Cortex
again. Depth is bounded at **1** by construction, and the `dic` rung inside the
fan-out still contributes DIC-native results — the interlock removes the
recursion, not the evidence.

**Its cost is reported, not hidden.** While a seam-initiated resolution is in
flight, a *concurrent unrelated* search on another thread also takes the direct
retriever. That is the pre-migration behaviour, so it is safe degradation — and
it is counted as `reentrant` in `run_stats()`.

That counter itself had to be process-wide, and the live run is what proved it:
the first measurement reported `reentrant: 0` across three governed searches that
each fanned out, because the fire was being tallied on the pool worker thread's
own thread-local run state and no caller ever reads that. `interlock_fires()` is
now process-wide alongside the depth it counts, and the re-measurement reports
`reentrant: 3` for three resolutions. Pinned by
`TestCycle::test_the_fire_count_is_process_wide_not_per_run`.

### A collection-scoped search declines, on purpose

`search(collection_id=...)` must return evidence from *that* collection.
`cortex.resolve` has no collection parameter: its `dic` rung calls
`engine.search(query, top_k, clearance)` with no scope, and `rag`, `graph`, `kb`
and `currency` have no notion of a DIC collection at all. A governed candidate
therefore carries no collection of record, so `search()`'s own post-filter
(`if collection_id and col_id != collection_id`) drops every one of them and a
scoped governed search returns **zero** where the direct retriever returned
results.

So a scoped ask declines the seam and takes the direct retriever, counted as
`declined_collection_scoped`. `honour_collection_scope` exists to measure that
drop, not to ship it.

---

## The wiki cache: REMOVED, not governed

`_file_qa_to_wiki` wrote high-confidence `answer()` results into the Claude Code
auto-memory directory as markdown, and `_check_wiki_cache` /
`_wiki_keyword_search` read them back **before** any retrieval ran. That put an
evidence source outside the database and outside the vector store, in front of
the canvas's mandatory chokepoint, where four controls this canvas exists to
enforce could not see it:

| Control | What the cache did |
|---|---|
| **Tenant** | Key was `sha256(collection_id \| query)` — no `tenant_id` anywhere in it, and the reader took no tenant. One tenant's answer was served verbatim to the next. |
| **Clearance** | A cached answer carried no document classification, so the clearance drop this file is careful to run *before* the `top_k` cap had nothing to filter and was bypassed entirely. |
| **Citations** | A hit returned `grounded=True` with an **empty** citation list and a `citation_quality` set to the filing threshold rather than measured. This module's own contract is that results are "never returned uncited"; the cache was the one path that returned an ungrounded answer labelled grounded. |
| **Freshness** | Files were never invalidated (`if topic_file.exists(): return`). A re-ingested or superseded document could not dislodge a cached answer. |

The fuzzy lane was worse than the exact one: at ≥ 0.70 keyword overlap it
returned a *different* question's answer as this question's.

It was also **inert**. Measured 2026-08-18 on the live deployment: **0 of 567**
files in the auto-memory directory carried the `dic-qa-` prefix, so the cache had
never filed or served a single answer. Removing it is behaviour-preserving in the
strict sense.

**Why not govern it instead.** A per-query answer cache already exists inside
Cortex (`cache.operations` in `args/cortex_config.yaml`), keyed by the governed
context and invalidated with it. Re-implementing one on the filesystem, in the
user's *cross-project* memory directory, would be a second cache to govern rather
than a governed cache.

Coverage moved to
`tests/test_dic_search_evidence.py::TestWikiCacheRemoved`, which asserts the
symbols are gone from **both** trees and that `answer()` cannot reach the
auto-memory directory by any route. The ACE and ANVIL wiki items in
`tests/test_wiki_integrations.py` are untouched.

### Two related fixes in the same surface

* **`answer()` gained a `clearance` parameter.** It had always called `search()`
  with *no* clearance, so a synthesized answer could be composed over evidence
  `search()` itself would have withheld from the same caller. Default `None`
  keeps existing callers unchanged.
* **A rung's own marking now tightens the clearance drop.** `_doc_meta` answers
  from `dic_documents` and returns `"CUI"` when there is no row — which there is
  not for a candidate from a rung that is not DIC. The governed path can return
  one (a `kb` entry surfaced as `icdev-tool-…` in the live comparison below), and
  taking `_doc_meta`'s default for it would hand a caller a marking the source
  never claimed. The effective marking is now the **more restrictive** of the
  document's own and the one the rung reported — max, not override, so it can
  only ever tighten. `SearchResult.classification` defaults to `"CUI"`, the same
  rank as `_doc_meta`'s default, so a candidate whose rung reported nothing is
  unchanged.

---

## What was compared, before and after

Live DIC canvas, 2026-08-18: **55 documents, 29 collections, 4,111 `rag_chunks`.**

### 1. Toggle off is byte-for-byte the old behaviour

Five queries × three clearances (`None`, `CUI`, `UNCLASSIFIED`) = 15 result sets,
run against the unmodified `main` checkout and against this branch with the
shipped config (`enabled: false`), comparing `(doc_id, citation.classification,
len(content))` per result:

```
keys: 15   differing: 0
```

That covers the clearance tightening as well: it is a no-op on this corpus.

### 2. Toggle on — same corpus, same clearance, warm process

| query | legacy | governed | shared docs | governed-only |
|---|---|---|---|---|
| `zero trust architecture` | 12.29s, n=5 | 4.04s, n=5 | 3/5 | `dic_doc_a09b99f4ed`, `icdev-tool-b71f250` |
| `continuous monitoring` | 0.21s, n=5 | 3.32s, n=5 | 3/5 | `dic_doc_a09b99f4ed`, `dic_doc_ffb4888649` |
| `access control policy` | 0.17s, n=5 | 4.19s, n=5 | 3/4 | `dic_doc_a09b99f4ed`, `icdev-tool-6e81e0c` |

```
run_stats: {"resolutions": 3, "capped": 0, "reentrant": 3,
            "declined_collection_scoped": 0, "cached_queries": 3, "depth": 0}
```

`reentrant: 3` is the cycle: each of the three resolutions fanned out into the
`dic` rung, which asked the seam again on a pool worker thread and was sent to the
direct retriever. `depth: 0` at the end is the interlock releasing cleanly.

**Read the table honestly.** The governed path is not strictly better:

* **Recall overlaps rather than subsumes.** Three of five documents are shared per
  query; the governed path adds two (including `kb` sources the DIC corpus does
  not contain) and drops two the direct retriever ranked. That is a *different*
  ranking over a wider corpus, not a superset.
* **Evidence text is shorter.** Median content length **1512 → 200 characters**.
  A citation snippet is capped at 200 by `search_service._SNIPPET_CHARS`, and the
  candidate's `content` on this path *is* that snippet — deliberate, and the same
  trade cef-di-03 documents: the text a result shows must *be* the text its
  citation records. It is also the single biggest reason this toggle ships off:
  it changes what the dashboard renders.
* **Latency is more consistent, not uniformly lower.** The legacy path's first
  query paid 12.3s and its next two 0.2s; the governed path was 3.3–4.2s
  throughout.

### 3. A collection-scoped ask declines

```
n=0   stats: {"declined_collection_scoped": 1, "resolutions": 0, "depth": 0}
```

The seam is not consulted at all, and the direct retriever answers.

---

## Rollback

Flip `cortex.enabled` to `false` in `args/dic_search_config.yaml`. Off means the
seam is never consulted — `_rag_search` runs the original `RAGRetriever` call and
`DICSearchEngine` behaves exactly as before. Do not revert the merge.

## Do not

* Do **not** add a second `cortex.*` call elsewhere in `DICSearchEngine`. Only
  `_rag_search` may consult the seam, through `_governed_candidates`. A second,
  unguarded call re-opens the cycle and will look fine in a single-threaded test.
* Do **not** replace the process-wide interlock with a thread-local one because
  it is "more precise". It is blind to the pool hop the cycle actually crosses.
* Do **not** "fix" a thin governed answer by raising the global Cortex timeouts
  in `args/cortex_config.yaml` — every Cortex consumer shares them.
* Do **not** reintroduce a filesystem cache in front of retrieval. Cortex's
  `cache.operations` is the governed one.
