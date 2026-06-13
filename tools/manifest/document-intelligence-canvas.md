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

## Verification

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/verifier.py` | CoT/CoD claim replay + citation validation + abstention gate. Every AI-generated draft passes through `verify()` before persisting. Pipeline: `validate_citations` (structural), claim extraction, per-claim CoT/CoD replay against cited chunk (LLM + deterministic lexical-overlap fallback), optional corrective retrieval, and disposition (strip unsupported claims or reject/abstain). Reuses `icdev.tools.rag.retriever.validate_citations` and `icdev.tools.rag.corrective_rag`. Air-gap safe — functions headless without an LLM provider. |

## Analytics & Discovery

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/analytics_engine.py` | DIC Analytics Engine — document-level analytics, pattern detection, anomaly detection, and scenario impact analysis over the KG and RAG layers. All queries use `get_connection()` so RLS applies. No LLM calls — pure graph and SQL analytics. |
| `tools/document_intelligence/explorer.py` | DIC KG "Buried Bodies" Explorer. Surfaces: orphaned documents (no collection/chunks/versions), single-owner tribal knowledge, undocumented KG dependencies, contradictions between overlapping docs, and superseded versions. All queries are RLS-filtered by `tenant_id`. No LLM calls — pure graph analytics. |

## Flask Blueprint

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/blueprint.py` | Document Intelligence Canvas Flask Blueprint. Registers all UI routes (`/document-intelligence/`, `/collections`, `/search`, `/review`, `/generate`, `/acoic`, `/finetune`, `/snippets`, `/templates`, `/notebook`, `/notebook/<id>`) and JSON API endpoints (`/api/ingest`, `/api/ingest/url`, `/api/ingest/youtube`, `/api/search`, `/api/chat`, `/api/collections`, `/api/review/<id>/approve|reject`, `/api/generate`, `/api/generate/study-guide`, `/api/generate/faq`, `/api/generate/timeline`, `/api/generate/audio`, `/api/outputs`, `/api/outputs/<id>`, `/api/mode`, `/api/iqe-query`). |

## DIC Canvas Synergy (DSYN) — Integration Config

| Artifact | Purpose |
|----------|---------|
| `args/dic_canvas_integrations.yaml` | Maps canvas_events `event_type` values to affected DIC collection tags, doc_types, priority, rationale, and patch_mode. Covers all 8 Tier-1 canvases (NDC, Network, ZIG, Compliance, SIPA, DevSecOps, CloudForge, AI-ify) plus DIC-internal events and crowdsource. Used by `canvas_adapter.py` to resolve which collections need AI-drafted suggestions when a canvas event fires. |
| `tools/document_intelligence/canvas_adapter.py` | Resolves canvas_events rows → affected DIC collections. Loads the integrations YAML (cached, mtime-aware), matches event_type (exact → prefix → fallback), queries dic_collections for tag overlap (Python-side intersection), returns `[{collection_id, matched_tags, doc_type, priority, rationale}]`. |
| `tools/document_intelligence/suggestion_store.py` | DSYN suggestion lifecycle: `create_suggestion()` → `get_pending_suggestions()` → `decide_suggestion()`. Manages `dic_suggestions` (mutable) and `dic_suggestion_decisions` (append-only, NIST AU). |
| `tools/genesis/reflexes/dic_integration.py` | Genesis reflex (15-min cadence) that polls canvas_events, calls canvas_adapter, drafts targeted patch suggestions via the DIC generation route, and queues them in dic_suggestions for HITL review. Idempotent — re-run never creates duplicates. |

## Knowledge Handoff

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/handoff.py` | DIC Knowledge Handoff Workflow. Multi-step guided session: initiate (departing owner + successor + destination collection) → auto-build agenda from explorer findings → interview prompts → captured answers → CoD-verified structured document generation per agenda area → write to destination collection with HITL-gated status. All outputs are AI-labeled `PENDING`; never auto-published. |

## Notebook — NotebookLM-Style View (dic-notebook-01)

Air-gap-first, dual-mode implementation porting open-notebook/NotebookLM essentials natively into DIC.

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/extractors.py::extract_url` | Fetch and extract text from any web URL. Online: full HTTP fetch + HTML strip. Air-gap: returns empty Extraction with warning. Called from `POST /api/ingest/url`. |
| `tools/document_intelligence/extractors.py::extract_youtube` | Extract transcript text from a YouTube video URL via `youtube-transcript-api`. Air-gap: returns empty with prompt to paste manually. Called from `POST /api/ingest/youtube`. |
| `tools/document_intelligence/output_generators.py` | Four AI output generators (study guide, FAQ, timeline, audio overview). Dual-mode: LLM route via `LLMRouter` when online; deterministic fallback (key sentences, regex date/definition extraction, pyttsx3 TTS) in air-gap. Persists to `dic_generated_outputs`. |
| `tools/dashboard/templates/document_intelligence/notebook.html` | NotebookLM-style three-panel UI: sources (left) + grounded chat (center) + AI outputs/generators (right). Mode badge shows Air-gap vs Online. IQE widget wired to `dic.generated_outputs`. |

### Tables

| Table | Description |
|-------|-------------|
| `dic_generated_outputs` | Stores all generated outputs (study guide, FAQ, timeline, audio). Columns: `id`, `output_type`, `collection_id`, `content_json`, `provider`, `status`, `audio_path`, `created_at`, `tenant_id`, `classification`. |

### Key API routes (added to `tools/document_intelligence/blueprint.py`)

| Route | Purpose |
|-------|---------|
| `GET /notebook`, `GET /notebook/<id>` | Renders the Notebook page for a collection |
| `GET /api/mode` | Returns mode info: `{mode, llm_available, provider, capabilities}` |
| `POST /api/ingest/url` | Ingest web URL into a collection |
| `POST /api/ingest/youtube` | Ingest YouTube transcript into a collection |
| `POST /api/generate/study-guide` | Generate study guide from collection chunks |
| `POST /api/generate/faq` | Generate FAQ (n Q&A pairs) from collection chunks |
| `POST /api/generate/timeline` | Generate timeline of events from collection chunks |
| `POST /api/generate/audio` | Generate audio overview (script + pyttsx3 TTS) from collection |
| `GET /api/outputs` | List all generated outputs for a collection |
| `GET /api/outputs/<id>` | Get a single output's parsed content |
