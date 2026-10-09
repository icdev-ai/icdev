# DIC grounded search asks ONE governed seam for its candidates (cef-di-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from icdev.tools.document_intelligence.search_evidence import resolve_evidence
  bundle = resolve_evidence("zero trust architecture", clearance="CUI")
  bundle.candidates[i] is described by bundle.citations[i]   # INDEX-ALIGNED
Toggle: `cortex.enabled` in args/dic_search_config.yaml — DEFAULT OFF, and off
means the seam is NEVER consulted, so the rollback is a flag flip and not a
merge revert. What migrated is DICSearchEngine._rag_search, ONE
RAGRetriever.search call — exactly one rung, while the currency store, the KG
and the KB held evidence about the same entities and were never asked.
ONLY *WHERE CANDIDATES COME FROM* MOVED. Everything search() does WITH a
candidate is untouched and still runs in the same order on both paths:
_chunk_meta/_doc_meta citation packing, the collection post-filter, THE
CLEARANCE DROP (still strictly BEFORE the top_k cap, so the cap fills with
accessible results), _rerank_by_attribution, then the cap. The clearance
ordering is preserved by NOT MOVING IT — the seam returns candidates in the
shape _rag_search already returned and hands them to the same loop. The BM25
air-gap fallback is likewise untouched and is still the floor under BOTH
paths.
THE CYCLE: Cortex's own `dic` rung IS DICSearchEngine.search()
(search_service.py::search_dic) and `dic` is in `resolve.backends`, so
search -> resolve -> dic rung -> search is real. It is cut by a PROCESS-WIDE
interlock in search_evidence, NOT a thread-local one: _run_backends submits
each backend onto a shared ThreadPoolExecutor, so the re-entrant call arrives
on a DIFFERENT thread and the thread-local guard cef-di-01/cef-di-03 correctly
use is structurally blind to it — it would pass a single-threaded test and
recurse in production, inside a BOUNDED pool. The rule: THE INNERMOST DIC
SEARCH INSIDE A RESOLVE FAN-OUT IS ALWAYS THE RAW RUNG. Depth is bounded at 1.
Its cost is reported, not hidden: while a resolution is in flight a concurrent
unrelated search also takes the direct retriever, counted as `reentrant` in
run_stats(). Do NOT add a second cortex.* call elsewhere in DICSearchEngine.
A COLLECTION-SCOPED SEARCH DECLINES, on purpose. cortex.resolve has no
collection parameter — its `dic` rung calls engine.search(query, top_k,
clearance) with no scope, and rag/graph/kb/currency have no notion of a DIC
collection — so a governed candidate carries no collection of record and
search()'s own post-filter would drop every one of them, returning ZERO where
the direct retriever returned results. `honour_collection_scope` exists to
MEASURE that drop, not to ship it.
Evidence text is SHORTER on the governed path: a citation snippet is capped at
200 chars by search_service.py and the candidate's content IS that snippet.
Deliberate (the rendered text must BE what the citation records) and part of
why the toggle ships OFF — it changes what the dashboard renders.
THE WIKI CACHE IS GONE, not governed. _file_qa_to_wiki wrote grounded answers
into the Claude Code auto-memory directory and _check_wiki_cache /
_wiki_keyword_search read them back BEFORE any retrieval. Its key was
sha256(collection_id|query) — NO TENANT — the reader took no clearance, a hit
returned grounded=True with an EMPTY citation list and a citation_quality set
to the filing threshold rather than measured, and nothing ever invalidated a
file. The fuzzy lane returned a DIFFERENT question's answer at >=0.70 keyword
overlap. It was also inert: 0 of 567 files in the live auto-memory directory
carried the `dic-qa-` prefix (measured 2026-08-18), so it had never filed or
served an answer and removing it is behaviour-preserving in the strict sense.
Governing it was rejected — Cortex already has a governed per-query cache
(`cache.operations`), and a second one on the filesystem in the user's
cross-project memory directory is a cache to govern, not a governed cache.
answer() gained a `clearance` parameter in the same change: it had always
called search() with NO clearance, so a synthesized answer could be composed
over evidence search() itself would have withheld.
