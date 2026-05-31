# Document Intelligence Canvas (DIC) — Tools

The 20th ICDEV canvas: its own RAG+KG over documents, grounded NO-LLM search
with citations, freshness tracking, HITL + AI-labeled generation, and
RBAC+ABAC+RLS access control.

## Ingestion

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/extractors.py` | Built-in air-gap-safe file extractors. Returns `Extraction(text, provider, content_type, page_count, title, metadata, warnings)`. Supports PDF (pypdf), DOCX (python-docx), XLSX (openpyxl), PPTX (python-pptx), PNG (pytesseract/easyocr), HTML (strip-html), TXT (plain read). All formats degrade gracefully — missing library yields `text=""` + warning rather than raising. Called by `ingest_orchestrator.py` before chunking. |
| `tools/document_intelligence/ingest_orchestrator.py` | Route a file → provider (by extension) → extract → REUSE `icdev.tools.rag.chunker.chunk_content` + `IngestionManager.ingest_source` to chunk/embed/upsert into the vector store → bridge each chunk into the KG via `rag_to_kg_ingester.ingest_chunk_to_kg` → write `dic_documents` + initial `dic_versions(origin='human_authored', status='approved')` + `dic_chunk_links` (rag chunk → doc + page/section). Stamps `tenant_id`/`classification` from the caller's security context on every row. |
| `python -m tools.document_intelligence` | Headless CLI: `--ingest <path> --collection <id> [--tenant ID] [--classification C] [--created-by U] [--no-embed] [--no-kg] [--json]`. |

### Key API

```python
from tools.document_intelligence.ingest_orchestrator import ingest_file
outcome = ingest_file(path, collection_id, tenant_id=None, classification=None,
                      created_by=None, embed=True, bridge_kg=True)
# -> IngestOutcome(doc_id, version_id, collection_id, source_id, provider,
#                  chunks, chunks_embedded, kg_entities, kg_relationships,
#                  tenant_id, classification, errors)
```

Embedding and KG bridging are best-effort: if the vector store / LLM router is
unavailable (air-gapped/headless), DIC rows are still written and the failure
is reported in `errors`, never raised.

### Tables

- `dic_documents` — one row per ingested document (doc_id, collection_id, source_id, filename, content_type, provider, content_sha256, page_count, tenant_id, classification).
- `dic_versions` — version history; initial row is `human_authored`/`approved`.
- `dic_chunk_links` — maps each rag chunk (`{source_id}_chunk_{i}`) back to the document + version + page/section.

> Requires dic-ingest-02 (multimodal providers) for binary formats; falls back
> to a built-in text/markup extractor when the provider package is absent.

## ACOIC — Drift → Document Impact → Regen → NIST Re-map

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/acoic.py` | Flagship compliance bridge (dic-acoic-01/02). `handle_drift(event)` records a canvas drift event, scores document impact, enqueues HITL regeneration, and re-maps affected NIST 800-53 controls. `map_changed_controls(ids)` cross-maps each control via the RICOAS/NIST 800-53 crosswalk engine (`tools.compliance.crosswalk_engine.get_frameworks_for_control` → FedRAMP/800-171/CMMC/ISO) + best-effort KG path (`compliance_graph.get_crosswalk_path`). `generate_ssp_fragment(control)` drafts a cited SSP narrative grounded ONLY in retrieved evidence, runs it through the DIC `verifier.verify` CoD/citation gate, and persists it `origin='ai_generated'`, `ai_labeled=1`, `status='pending_review'` (HITL-gated). `approve_fragment`/`reject_fragment` are the human review actions. `get_acoic_page_context()` feeds the `/document-intelligence/acoic` page. |

### Key API

```python
from tools.document_intelligence import acoic
acoic.handle_drift({"source": "ndc", "severity": "critical",
                    "document_id": "dic_doc_42", "control_ids": ["AC-2"]})
acoic.map_changed_controls(["AC-2", "AU-3"])      # cross-framework re-map
frag = acoic.generate_ssp_fragment("AC-2", document_id="dic_doc_42")  # CoD-verified
acoic.approve_fragment(frag["fragment_id"], reviewed_by="ato_lead")   # HITL
acoic.get_acoic_page_context()                    # {drift_events, regen_queue, ssp_fragments}
```

CLI: `python -m tools.document_intelligence.acoic {drift|map|fragment|approve|reject|queue|fragments|page} [...] [--json]`.

### Tables

- `dic_drift_events` — recorded canvas drift events (source, entity, severity, payload, processed).
- `dic_acoic_regen_queue` — impacted documents awaiting HITL regeneration (impact_level/score, state ∈ queued/regenerating/drafted/approved/rejected, ssp_fragment_id).
- `dic_ssp_fragments` — drafted SSP narratives (control_id, frameworks_json, fragment_text, `origin='ai_generated'`, `ai_labeled=1`, verified/abstained, citations + CoD verdict, status ∈ pending_review/approved/rejected). All carry `tenant_id`/`classification` (RLS-compatible).

> SSP drafting abstains rather than hallucinate when no grounded evidence is
> retrieved for a control — correct behavior until documents are ingested.
> The `/document-intelligence/acoic` route is wired by the DIC blueprint
> (dic-ui-02); `acoic.get_acoic_page_context()` is the data source.

## Search

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/search_engine.py` | DIC Grounded Search Engine. Default mode: BM25 + KG traversal (NO LLM, air-gap safe). Optional hybrid mode adds vector similarity + RRF fusion + cross-encoder rerank. Every result carries a mandatory citation pack; results with no traceable source are suppressed. `DICSearchEngine.search(query, collection_id, top_k, mode)` returns `list[DICSearchResult]` each with a `Citation` (doc_id, title, version_id, page, section, chunk_id). Falls back to pure SQL BM25 (`rag_chunks`) when the vector store is unavailable. |

### Key API

```python
from tools.document_intelligence.search_engine import DICSearchEngine

engine = DICSearchEngine(tenant_id="default")
results = engine.search("AC-2 access control policy", collection_id="ato_docs", top_k=5)
for r in results:
    print(r.citation.doc_title, r.citation.page, r.score)
# mode="hybrid" enables vector+rerank when RAGRetriever is available
```

## Generation

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/doc_generator.py` | DIC AI-Assisted Document Generator. `generate_document(query, collection_id)` retrieves top chunks via `DICSearchEngine`, builds a grounded LLM outline (≤6 sections), drafts each section via LLM, CoT/CoD-verifies each draft against retrieved evidence (strips unsupported claims; abstains when evidence is insufficient), and writes `dic_documents` + `dic_versions(origin='ai_generated', status='pending_review')` + per-section `dic_sections` rows — all HITL-gated and AI-labeled, never auto-published. `regenerate_section(version_id, heading, collection_id)` re-queries the collection using the section heading as the query, drafts a replacement with targeted evidence + adjacent-section coherence context, CoD-verifies it, and upserts the `dic_sections` row + reassembles the version SHA. Air-gap safe: falls back to abstention when the LLM router is unavailable. |

### Key API

```python
from tools.document_intelligence.doc_generator import generate_document, regenerate_section

# Full document generation (returns GenerateResult with sections + version_id for HITL)
result = generate_document(
    "AC-2 access control policy",
    collection_id="ato_docs",
    tenant_id="default",
    classification="CUI",
    created_by="analyst",
)
print(result.title, result.version_id, len(result.sections))

# Per-section regeneration (returns dict with new content + citation_count)
update = regenerate_section(
    version_id=result.version_id,
    heading="Overview",
    collection_id="ato_docs",
)
print(update["content"], update["citation_count"], update["status"])
```

### Tables written

- `dic_documents` — one row per AI-generated document (doc_id keyed on SHA256 of query+collection).
- `dic_versions` — version row with `origin='ai_generated'`, `status='pending_review'` (HITL-gated).
- `dic_sections` — one row per section (heading, content, citations_json, status, origin); supports per-section regeneration and per-section HITL review.

> All three tables carry `tenant_id`/`classification` (RLS-compatible). Requires
> `DICSearchEngine` (search_engine.py) and optionally `verifier.verify`
> (verifier.py) for CoD gating — both are soft dependencies; generation
> degrades gracefully when either is absent.
