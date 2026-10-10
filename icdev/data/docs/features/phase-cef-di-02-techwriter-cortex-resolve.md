# CUI // SP-CTI

# cef-di-02 — the tech writer's retrieval half on `cortex.resolve()`, and the `collection_id` that was never passed

Phase 3 of the Cortex Evidence Fabric (`plans/cortex-evidence-fabric-plan.md`).
One surface, one PR, behind one toggle.

Surface: `tools/document_intelligence/tech_writing_assist.py::research_and_draft()`
— the AI research + drafting call behind
`POST /document-intelligence/api/techwriter/research`, used by the section
"Research" panel in the Tech Writer editor.

---

## 1. The defect this card names, reproduced on the live corpus

`research_and_draft` has accepted `collection_id` since it was written and
passed it to nothing. RAG was scoped by tenant alone
(`RAGRetriever(tenant_id=...)`, `search(query, top_k=...)`) and the knowledge
graph was not scoped at all (`kg_retrieve(query, top_k=..., compress=False)`).
The parameter looked like a control and was decoration.

Measured 2026-08-18 against the live PostgreSQL corpus (55 documents,
29 collections), query `"BGP peering and routing configuration"`, drafting a
section of a document in the `default` collection (17 documents):

| call | chunks | distinct docs | in the requested collection? |
|---|---|---|---|
| **before** — `collection_id` ignored | 8 | `dic_doc_1e9321546e438333` | **no** — that document belongs to `isp-peering-demo` |
| **after** — scoped to `default` | 8 | `dic_doc_b72241728c8b2609`, `dic_doc_b8cb5f221cfaed5b` | yes |
| **after** — scoped to `isp-peering-demo` | 8 | `dic_doc_1e9321546e438333` | yes |

So the pre-change behaviour was not theoretical: every chunk the editor
retrieved for that section came from a *different collection's* document, the
draft cited it, and nothing went red. A regression here looks exactly like good
recall, which is why the test that pins it is gated
(`args/ci_test_files/core.d/cef-di-02.txt`).

### How the scope is enforced — two places, not redundant

* **Natively**, by passing the scope each retriever already accepts.
  `rag_chunks.project_id` is the DIC collection of record — `ingest_orchestrator`
  writes `project_id=collection_id` and `DICSearchEngine._rag_search` already
  filters on it — and `kg_graphs.project_id` carries the collection for a
  DIC-derived graph. Verified on the live corpus: of the 434 `dic_documents`
  chunks that join to a live document row, `project_id` equals that document's
  `collection_id` **434 times and disagrees 0 times**. Passing it means the
  retriever spends its `top_k` budget on in-scope chunks.
* **At this surface**, by dropping every retrieved source that does not name a
  document in the collection. A retriever that ignores the parameter — a stub,
  a future backend, or the Cortex fan-out, which has no collection filter at
  all — would otherwise put the defect straight back while the call still
  *looked* scoped. This is the half the tests pin, and it is the same
  two-place shape `DICSearchEngine.search` already uses.

**Fail closed.** When membership cannot be read, nothing is in scope — an
unverifiable scope is not a scope. `set()` (an empty collection) and `None`
(could not check) are different answers and are never merged: the first is a
fact about the corpus, the second is an outage, and they send you to different
fixes. Both are reported on `ResearchResult.scope`, never silent.

**An empty `collection_id` requests no scope at all**, and every path is then
byte-for-byte what it was before. `blueprint.py` stopped defaulting an absent
field to the literal `"default"`: harmless while the parameter was ignored,
and a silent confinement to a collection that happens to be named `default`
now that it is not.

One adjacent bug fixed on the way, because the scope needs it: the legacy RAG
lane read `getattr(sr, "doc_id", "")`, which a real `rag.SearchResult` does not
have (it carries `source_id`). Every chunk this surface registered was
therefore labelled `"document"`, and there was no doc id to scope on. It now
reads `doc_id` first — for the adapters and fakes that publish it — and falls
back to `source_id`.

---

## 2. The migration: retrieval through `cortex.resolve()`

`args/dic_techwriter.yaml` → `cortex.enabled`, **default false**.

```yaml
cortex:
  enabled: false        # master toggle — DEFAULT OFF (never remove)
  top_k: 8
  scope_overfetch: 4    # recall knob under a collection scope; never a scope knob
  max_top_k: 40
```

With it on, `research_and_draft` stops hand-wiring `RAGRetriever` and
`graph_rag` and asks one governed seam:

```python
cortex.resolve(entity=query, question=section_heading,
               ctx=CortexContext(domain="document_intelligence", air_gap=...))
```

which inherits the 8-gate TRUST chain, the `cortex_audit` row, the
`source_citation_registry` row for the evidence set, and a deterministic
currency verdict for the subject — none of which the hand-wired chain had.

Decisions worth stating:

* **`document_intelligence` lens, not `document`** (cef-bck-04). It intersects
  resolve's rung set down to `[rag, dic]` and row-scopes to the `dic_` source
  prefixes, so the fan-out answers about the DIC corpus rather than about the
  3,552 compliance-corpus chunks that share `rag_chunks`. It is deliberately
  *not* the lens the DRAFT runs under — `cortex.complete()` keeps
  `domain="document"`, and changing that would change the drafting voice, which
  this card is not about.
* **A refusal does not fall back to the legacy chain.** A governance chain you
  can route around by failing is decoration, and this evidence is the draft's
  grounding rather than a supplement to it. The refusal is reported on
  `ResearchResult.resolution["blocked"]` and in `warnings`, and the draft
  proceeds with whatever survived — possibly nothing. (This differs from
  cef-di-01, where the evidence genuinely *was* supplementary to a verdict the
  pack derives itself.)
* **No egress widening.** The lens keeps the `external` rung out of the
  fan-out, and the caller's own air-gap verdict is threaded onto the context as
  well.
* **The verdict is surfaced, not injected.** `verdict` / `gaps` / `conflicts`
  land on `ResearchResult.resolution` and the actionable ones become warnings
  ("`TLS 1.1` is deprecated — the draft may be describing something that has
  been replaced"). Feeding a deterministic verdict back into a generative
  prompt is how it stops being one.
* **A dead rung is not an empty collection.** `backend_errors` produces its own
  warning, because thin evidence from a cold embedding provider otherwise
  reads to the writer as a statement about their corpus.

---

## 3. What changed behaviourally, measured — the toggle is off for a reason

Same collection (`default`), same section heading, retrieval only (no LLM),
live corpus, 2026-08-18:

| query | legacy (scoped) | cortex (scoped) |
|---|---|---|
| `security controls and access management` | 7 sources, 5 docs, 8 KG entities, **33,380** chars | 8 sources, 8 docs, 0 KG, **1,600** chars |
| `network architecture and segmentation` | 7 sources, 3 docs, 8 KG entities, **53,019** chars | **0 sources** |
| `system overview and interfaces` | 1 source, 1 doc, 8 KG entities, **8,021** chars | 2 sources, 2 docs, 0 KG, **400** chars |

The migrated chain is a **breadth-for-depth trade**, and two structural causes
explain all of it. Neither is a bug in this card's code, and both are worth
recording because cef-di-04 and cef-di-05 migrate retrieval surfaces onto the
same seam and will meet them:

1. **The fan-out is document-granular, not chunk-granular.**
   `search_service.fusion_ident` fuses on `citation.source_id`, which for a RAG
   hit is the *document* id. That is correct and deliberate for entity
   resolution — "one document retrieved by `rag` and by `dic` is ONE source",
   and counting it twice would let a chunk corroborate itself — but it means 8
   retrieved chunks of one document collapse to 1 citation. Measured directly:
   `search_rag(top_k=32)` returned 32 hits with **1** distinct `source_id`, and
   the fused set was 1.
2. **A citation carries a 200-char snippet** (`search_service._SNIPPET_CHARS`),
   where the legacy chain injects up to 800 chars of chunk text. A resolution
   returns citations, not hits, so the full chunk text is not reachable through
   the seam.

Plus one consequence of the lens itself: `document_intelligence` declares
`backends: [rag, dic]`, so the KG lane contributes nothing on the migrated
path. That is cef-bck-04's stated decision (a `dic_` row scope deletes 100% of
`kg_nodes` hits, so declaring `graph` would buy a round trip and an inflated
`filtered_out`, not evidence).

**Conclusion:** the migrated chain is governed, audited, cited and
verdict-bearing, and it currently supplies materially less grounding text than
the chain it replaces. That is why it ships **off**, and why the finding is
written down here rather than discovered by whoever flips it. The fix is
Cortex-side (a chunk-granular evidence view, or a configurable snippet length
on the citation) and is deliberately not attempted in a DI-surface card.

---

## 4. What did NOT change

* the air-gap gate, which still fails safe to "air-gapped" when the detector
  raises, and still gates the web-fetch step and only that step. Asserted on
  **both** chains.
* the deterministic post-draft checks: `content_grounding.find_placeholders`,
  `validate_standards_references` (`args/tw_standards_whitelist.yaml`), and the
  `citation_grounding.validate_citations` report over the numbered source
  register. They sit after every drafting path (CoD, single-shot, Cortex) and
  are unchanged.
* the drafting half, which has routed through the governed `cortex.complete()`
  facade since the adoption pilot.
* the `[source: N]` register shape. Both chains register through
  `_register_source`, so the draft's citations resolve identically and the
  citation report grades the same way regardless of which chain ran.

---

## 5. Files

| File | Change |
|---|---|
| `args/dic_techwriter.yaml` | **new** — the `cortex:` toggle block for this surface |
| `tools/document_intelligence/tech_writing_assist.py` | collection scope (`_Scope`, `_collection_doc_ids`), the `_cortex_retrieve` seam, native scope pass-through on the legacy chain, `ResearchResult.retrieval_path` / `.scope` / `.resolution` |
| `tools/document_intelligence/blueprint.py` | absent `collection_id` → unscoped (not `"default"`); the three new fields returned so the scope is observable |
| `icdev/tools/document_intelligence/*` | mirrored |
| `tests/test_dic_techwriter_cortex_scope.py` | **new** — 15 tests |
| `args/ci_test_files/core.d/cef-di-02.txt` | gates the new file, in its own fragment |
| `tools/manifest/document-intelligence-canvas.md` | registration row updated (+ mirror) |

## 6. Rollback

Set `cortex.enabled: false` in `args/dic_techwriter.yaml` — the shipped
default. The seam is then never consulted (asserted by
`test_cortex_toggle_off_never_consults_the_seam`, which fails the test if
`cortex.resolve` is called at all, rather than checking that its result was
ignored). The collection scope is **not** behind the toggle: it is the defect
fix and applies to both chains.
