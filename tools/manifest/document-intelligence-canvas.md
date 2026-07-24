# Document Intelligence Canvas (DIC) — Tools

The 20th ICDEV canvas: its own RAG+KG over documents, grounded NO-LLM search
with citations, freshness tracking, HITL + AI-labeled generation, and
RBAC+ABAC+RLS access control.

## Ingestion

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/extractors.py` | Built-in air-gap-safe file extractors. Returns `Extraction(text, provider, content_type, page_count, title, metadata, warnings)`. Supports PDF (pypdf), DOCX (python-docx), XLSX (openpyxl), PPTX (python-pptx), PNG (pytesseract/easyocr), HTML (strip-html), TXT (plain read). All formats degrade gracefully — missing library yields `text=""` + warning rather than raising. Called by `ingest_orchestrator.py` before chunking. MarkItDown is tried first for DOCX/PPTX/XLSX/images/audio when installed (see converter below). |
| `tools/document_intelligence/converters/markitdown_adapter.py` | Optional enhanced extractor wrapping Microsoft MarkItDown (pip install markitdown). Converts DOCX/PPTX/XLSX/PDF/HTML/images/audio to structured Markdown with header/table preservation. Gracefully degrades to `Extraction(text="", provider="markitdown-unavailable")` when library is absent. `should_use_markitdown(ext)` guards the dispatch in `extract_file()`. `SUPPORTED_EXTENSIONS` frozenset lists all handled formats; `_PREFER_BUILTIN` excludes .txt/.md/.py (adapt-md-02/03). |
| `tools/document_intelligence/collection_registry.py` | `ensure_collection(conn, collection_id, *, name, tenant_id, classification)` — get-or-create the `dic_collections` row before a document is written. `dic_documents.collection_id` is free-text with no FK and every ingest path takes it from the caller (`/api/ingest` defaults to `"default"`, the CLI passes `--collection` verbatim, the IDR flow mints `idr-<session_id>`); the Collections UI enumerates `dic_collections`, so a document whose collection has no row is ingested, chunked, embedded, scanned — and unreachable. Call it before any `dic_documents` INSERT. Does not commit (caller owns the transaction) and does not swallow errors (a swallowed failure poisons the PG transaction and resurfaces on the document INSERT). Returns False for an empty id rather than inventing one. `most_restrictive(*classifications)` ranks markings explicitly — classification does NOT sort alphabetically, so `MAX()` over raw text would rank `UNCLASSIFIED` above `SECRET`. Repair for existing rows: migration 268. |
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
> The `/document-intelligence/docdrift` route is wired by the DIC blueprint
> (`/acoic` 301-redirects; the module and tables keep the legacy name on purpose —
> see the acoic.py docstring)
> (dic-ui-02); `acoic.get_acoic_page_context()` is the data source.

## Search

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/search_engine.py` | DIC Grounded Search Engine. Default mode: BM25 + KG traversal (NO LLM, air-gap safe). Optional hybrid mode adds vector similarity + RRF fusion + cross-encoder rerank. Every result carries a mandatory citation pack; results with no traceable source are suppressed. `DICSearchEngine.search(query, collection_id, top_k, mode)` returns `list[DICSearchResult]` each with a `Citation` (doc_id, title, version_id, page, section, chunk_id). Falls back to pure SQL BM25 (`rag_chunks`) when the vector store is unavailable. **Karpathy wiki integration:** `DICSearchEngine.answer()` checks the memory wiki for a cached grounded Q&A before running RAG (`_check_wiki_cache`) and files high-confidence answers back to the wiki after synthesis (`_file_qa_to_wiki`); `_wiki_keyword_search()` enables fuzzy cache lookup. |

### Key API

```python
from tools.document_intelligence.search_engine import DICSearchEngine

engine = DICSearchEngine(tenant_id="default")
results = engine.search("AC-2 access control policy", collection_id="ato_docs", top_k=5)
for r in results:
    print(r.citation.doc_title, r.citation.page, r.score)
# mode="hybrid" enables vector+rerank when RAGRetriever is available
```

## Freshness

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/freshness_engine.py` | DIC Freshness Engine — staleness scoring and autonomous reflex trigger. `scan_collection(collection_id, ...)` scores every document in a collection across four dimensions: document age vs retention tier (default 90 days), time since last approved version, drift events since last update, and pending-review section count. Writes per-doc `dic_doc_freshness` rows (upsert) and a collection-level `dic_freshness_scans` row; returns `ScanResult` with `stale_count`, `aging_count`, `fresh_count`, `regen_priority`. `corpus_heatmap(tenant_id, limit)` returns all documents ordered by score descending (stale first) for the dashboard heatmap. All reads use `get_connection()` so RLS applies. |
| `tools/document_intelligence/freshness_notifier.py` | DIC Freshness Notifier (dmx-loop-01) — proactive owner alerts on state crossings. `notify_freshness_crossings(results, prior_states, *, conn, tenant_id, config, gateway, now)` fires an owner/steward notification via `tools/notifications/gateway.py` the first time a document CROSSES into `aging`/`stale` (crossing-only; a doc already stale does not re-alert). Per-document cooldown persisted in `dic_doc_freshness.last_notified_at` (mutable — not append-only). Owner resolved from `dic_collections.owner_id`, else the configured `default_channel`. Body links to the modernization page + lists top findings. Config lives in `args/docmod/docmod_config.yaml` (`freshness_notifications`, DEFAULT OFF). Notify-only (no edits); air-gap safe (unreachable channel logs + skips). Invoked (gated) from `scan_collection`. |

### Key API

```python
from tools.document_intelligence.freshness_engine import scan_collection, corpus_heatmap

result = scan_collection("ato_docs", tenant_id="default", classification="CUI")
print(result.stale_count, result.regen_priority)
for doc in result.docs:
    print(doc.doc_id, doc.state, doc.score, doc.reason)

heatmap = corpus_heatmap(tenant_id="default", limit=100)
# -> [{"doc_id", "collection_id", "state", "reason", "score", "title"}, ...]
```

### Tables

- `dic_doc_freshness` — per-doc freshness row (doc_id PK, collection_id, state ∈ fresh/aging/stale/unknown, reason, source_event, score 0–1, updated_at, tenant_id, classification).
- `dic_freshness_scans` — per-collection scan summary (scan_id, collection_id, stale_count, regen_priority, scanned_at, tenant_id).

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

## Style Enforcement

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/style_engine.py` | DIC Style Engine — deterministic "one voice" gate. Checks document section text against configurable rules in `args/dic_style_rules.yaml`. No LLM required; all checks are regex + heuristic (air-gap safe). `check_style(text)` returns `StyleResult(score, passed, violations, stats)` where each `Violation` carries `rule_id`, `severity` (error/warning/info), `message`, `suggestion`, and `match`. `check_sections(sections)` accepts a list of `{heading, content}` dicts and returns an `overall_score` plus per-section results. Rule types: `forbidden_terms`/`replacement_terms` (regex term matching), `passive_ratio` (passive-voice sentence ratio), `sentence_length` (avg + per-sentence word count), `acronym_check` (undefined first-use detection). Score starts at 100 and deducts per violation (error: −15, warning: −5, info: −1); passing threshold is configurable via `meta.passing_score` in the YAML (default 70). |
## Edit History

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/history_recorder.py` | Append-only NIST AU audit trail for DIC section content changes. `record_edit(section_id, editor, content_before, content_after, ...)` skips no-ops (before == after), computes a `char_delta`, generates a truncated unified diff via stdlib `difflib`, and inserts into `dic_edit_history` (immutable — no UPDATE/DELETE). When `|char_delta| > 50`, best-effort calls `consistency_checker.extract_changed_concepts` + `find_related_docs` and emits `dic.consistency_flag` canvas events to related documents. `get_section_history(section_id, limit, since)` returns edit rows most-recent first. All rows carry `tenant_id`/`classification` (RLS-compatible). |

### Key API

```python
from tools.document_intelligence.style_engine import check_style, check_sections

result = check_style("The Contractor will utilize AI to facilitate...")
print(result.score, result.passed, result.violations)

report = check_sections([{"heading": "Overview", "content": "..."}])
print(report["overall_score"], report["passed"])
```

> Rules file: `args/dic_style_rules.yaml` — add/disable rules there without touching code.
> Called by `doc_generator.py` after CoD verification to enforce "one voice" before persisting sections.

## Filtering

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/filters.py` | DIC Adaptive Document Filters — replaces hardcoded relevance/size/age thresholds with statistically-derived bounds (IQR fence and Z-score) computed from the live corpus. `filter_by_relevance(docs, scores)`, `filter_by_size(docs)`, and `filter_by_age(docs)` return filtered lists; anomalous outliers are flagged for HITL review rather than silently dropped. `anomaly_report(docs)` returns a dict summarising which documents triggered each filter and why. Falls back to conservative constants (`_MIN_RELEVANCE`, `_MAX_DOC_SIZE_MB`, `_MAX_AGE_DAYS`) when the corpus is too small for statistical bounds (< 4 samples). No LLM calls — pure statistics (stdlib `statistics`). |
from tools.document_intelligence.history_recorder import record_edit, get_section_history

edit_id = record_edit("sec_abc123", "alice", old_content, new_content)
# Returns new edit_id str, or None if before == after (no-op)

history = get_section_history("sec_abc123", limit=20, since="2026-01-01T00:00:00+00:00")
# Returns list of dicts: edit_id, section_id, doc_id, version_id, editor, char_delta, diff_summary, edited_at, classification
```

### Table

- `dic_edit_history` — append-only audit log (edit_id, section_id, doc_id, version_id, editor, content_before, content_after, char_delta, diff_summary, edited_at, tenant_id, classification). `_ensure_table()` creates it on first use.

## Freshness

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/freshness_engine.py` | DIC Freshness Engine — staleness scoring and autonomous reflex trigger. `scan_collection(collection_id, *, tenant_id, classification, retention_days)` scores every document in a collection across four dimensions: age vs retention tier, time since last approved version, drift events since last update, and pending-review section count. Returns `ScanResult` with per-doc `FreshnessResult` list (state ∈ `fresh`/`aging`/`stale`/`unknown`, composite score 0.0–1.0) and aggregate `stale_count`/`aging_count`/`fresh_count`/`regen_priority`. Persists per-doc rows to `dic_doc_freshness` (upsert) and a collection-level row to `dic_freshness_scans`. Air-gap safe — no LLM calls; pure date arithmetic + SQL. Feeds the `/document-intelligence/` heatmap and `dic_digest.py` weekly reflex. |

### Key API

```python
from tools.document_intelligence.freshness_engine import scan_collection

result = scan_collection("ato_docs", tenant_id="default", classification="CUI")
print(result.stale_count, result.regen_priority)
for doc in result.docs:
    print(doc.doc_id, doc.state, doc.score, doc.reason)
```

### Tables written

- `dic_doc_freshness` — per-doc freshness row (doc_id PK, collection_id, state, reason, source_event, score, updated_at, tenant_id, classification). Upserted on every scan.
- `dic_freshness_scans` — collection-level aggregate (scan_id, collection_id, stale_count, regen_priority, scanned_at, tenant_id).

## Analytics & Discovery

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/analytics_engine.py` | DIC Analytics Engine — document-level analytics, pattern detection, anomaly detection, and scenario impact analysis over the KG and RAG layers. All queries use `get_connection()` so RLS applies. No LLM calls — pure graph and SQL analytics. |
| `tools/document_intelligence/explorer.py` | DIC KG "Buried Bodies" Explorer. Surfaces: orphaned documents (no collection/chunks/versions), single-owner tribal knowledge, undocumented KG dependencies, contradictions between overlapping docs, and superseded versions. All queries are RLS-filtered by `tenant_id`. No LLM calls — pure graph analytics. |
| `tools/document_intelligence/consistency_checker.py` | Cross-document concept overlap detector for propagating review flags when source content changes. `extract_changed_concepts(before, after)` returns new noun phrases (no-NLTK tokenizer, stop-word filtered, capped at 50 terms). `find_related_docs(doc_id, changed_concepts, tenant_id, limit)` walks `kg_nodes`/`kg_graphs` Python-side (avoids SQL JSON dialect issues) to find docs sharing concept nodes with the changed document. Returns `[{doc_id, doc_title, collection_id, last_updated, matching_concepts}]`. All KG reads use `get_connection()` so RLS applies. |
| `tools/document_intelligence/cross_reference_tracker.py` | Inter-document cross-reference tracking + cascade flagging (dmx-ref-01). Complements `consistency_checker` (KG concept overlap) by tracking EXPLICIT textual references ("see Section 3 of the Backup SOP", "per <Title> §N"). Deterministic regex — no LLM, air-gap safe; patterns live in `tools/doc_modernization/constants.REFERENCE_PATTERNS` (extensible). `extract_references(text, source_doc_id, source_section)` → ref dicts; `store_references_from_text(...)` / `store_references(conn, doc_id)` upsert into `dic_cross_references` (idempotent via deterministic id) — wired into `ingest_orchestrator.ingest_file` at ingest; `resolve_references(conn, tenant_id)` matches `target_doc_ref` to a known doc by title/filename and fills `target_doc_id`, raising a `dangling_reference` finding for unresolved refs; `cascade_on_version_approval(version_id, conn)` raises a `cross_reference_cascade` finding on each citing document whose inbound reference points at a section that changed on approval (wired into the DIC review-approve route). Findings are written append-only to `docmod_findings` with a stable `dedupe_key` so they flow through `drift_bridge` → ACOIC and `get_findings` dedup unchanged (HITL-preserving — findings only, never edits). `dic_cross_references` carries `tenant_id`/`classification` (RLS) and is NOT append-only (resolution UPDATEs `target_doc_id`). CLI: `python tools/document_intelligence/cross_reference_tracker.py --backfill|--resolve --json`. |

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

## Concurrency & Conflict Detection

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/conflict_detector.py` | DIC Section Conflict Detector — optimistic-concurrency check on content saves. `compute_hash(content)` returns a CRC32 hex fingerprint (zlib, not cryptographic — avoids SIPA `_CRYPTO_HASHLIB` false positive). `get_section_state(conn, section_id)` fetches the live content + hash for a `dic_sections` row. `check_conflict(conn, section_id, expected_hash)` compares the client's fingerprint against the DB state and returns `{conflict, current_hash, current_content}` — callers return HTTP 409 with `current_content` so the client can show a merge-resolution modal. Uses the caller's existing connection; opens no new DB connection. |
| `tools/document_intelligence/lock_manager.py` | DIC Section Lock Manager — pessimistic locking for collaborative editing. Prevents two editors from clobbering the same section simultaneously via a `dic_section_locks` DB table with TTL-based expiry (default 300 s). `acquire_lock(section_id, user_id, ttl_seconds, doc_id)` returns the lock dict on success, None if already locked by another user, or renews the TTL if the caller already holds it. `release_lock(section_id, user_id)` deletes the row if the caller owns it. `renew_lock(section_id, user_id, ttl_seconds)` extends the TTL in-place. `get_lock(section_id)` returns the active lock dict (auto-purging expired rows) or None. `purge_expired_locks()` sweeps stale rows and returns the count removed. All writes use `get_connection()` (RLS-aware); no WebSocket dependency — clients renew via periodic PUT. |
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
| `POST /api/generate/tasks` | Extract action items from study_guide/faq output → seed kanban tasks via task_factory. Returns {task_ids, count}. |
| `POST /api/generate/slides` | Convert study_guide or timeline output → slide deck (pptx_builder). Returns {deck_id, url}. |
| `POST /api/generate/roadmap` | Push timeline events to PMO milestones via milestone_manager. Body: {output_id, contract_id}. |
| `POST /api/generate/enhance` | Layer LLM narrative on a BM25+KG output. Returns enhanced content_json. |
| `POST /api/collections/<id>/attach-coworker` | Register DIC collection as ACE co-worker context; returns {coworker_url}. |

## Provenance

| Tool | Purpose |
|------|---------|
| `tools/dic/provenance_adapter.py` | DIC Provenance Adapter — bridges DIC search results to `provenance_engine` metadata for footnote popover annotation (irad-aidp-09). `get_chunk_provenance(chunk_uuid, chunk_text, llm_output)` returns `{sha256, classification, source_doc_uuid, version_tree_ref, ingest_timestamp, attribution_score}`. Attribution score is a deterministic token-overlap recall ratio (chunk tokens ∩ output tokens / chunk tokens) — no LLM calls. Queries `rag_provenance_ledger` via `provenance_engine.get_lineage()` (irad-aidp-02); falls back to a direct DB SELECT on `rag_provenance_ledger` when the engine is unavailable. |

### Key API

```python
from tools.dic.provenance_adapter import get_chunk_provenance

prov = get_chunk_provenance(
    chunk_uuid="abc-123",
    chunk_text="The system shall ...",
    llm_output="Access control policies require ...",
)
# -> {sha256, classification, source_doc_uuid, version_tree_ref,
#     ingest_timestamp, attribution_score}
```

## MCP Dispatch

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/gap_handlers.py` | Thin MCP gap-handler wrappers for DIC MCP dispatch. Exposes four handlers: `handle_dic_ingest(params)` → `ingest_orchestrator.ingest_document`, `handle_dic_search(params)` → `search_engine.search`, `handle_dic_generate(params)` → `doc_generator.generate`, `handle_dic_chat(params)` → `search_engine.answer`. All handlers catch exceptions and return structured error dicts rather than raising, making them safe for MCP gateway dispatch. Registered in `tools/mcp/tool_registry.py` and `tools/mcp/gap_handlers.py`. |

## Ecosystem Integration Tools

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/canvas_push.py::push_artifact` | Any canvas calls this to ingest its artifacts (PDF/HTML/text) into DIC collections. |
| `tools/genesis/reflexes/dic_digest.py::run` | Weekly reflex: new-doc summary + freshness alerts → notification_log. Registered in daemon.py REFLEX_NAMES. |
| `tools/research/source_scanners/dic_scanner.py::scan_dic_collection` | Research engine scanner: queries rag_chunks from a DIC collection, maps to research_signals format. Key: "dic_collection". |
| `tools/canvas/kg_builder.py::upsert_from_dic` | Post-generation KG bridge: writes DIC entities/relationships to canvas_kg_nodes/edges with canvas='dic'. |

## Tech Writer Workspace (Migration 230)

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/tech_writing_assist.py` | AI research + drafting + diagram generation for the Tech Writer workspace. `research_and_draft(query, section_heading, template_type, ...)` → `ResearchResult`; `generate_diagram_syntax(description, diagram_type, template_type)` → `DiagramResult`. Never raises — all errors surface in result.error. Uses module-level optional imports (RAGRetriever, kg_retrieve, LLMRouter, fetch_content, is_airgap) so tests can patch them. Air-gap aware (skips web when air-gapped). `validate_standards_references(text)` — deterministic whitelist check (args/tw_standards_whitelist.yaml) of NIST SP 800-*, CMMC, FedRAMP, DISA SRG/STIG citations in the draft's References section; warnings land in `ResearchResult.warnings` and the WriteGuard sidebar. ARCH_* drafts route through `ChainOrchestrator.invoke_chain_of_debate` with single-shot fallback when `ICDEV_TW_COD_ENABLED=true` (default off). |

Routes added to `blueprint.py`:
- `GET /techwriter` — Tech Writer workspace page (6 template-type cards + continue-writing list)
- `PATCH /api/documents/<id>/writeguard-mode` — update WriteGuard content mode
- `POST /api/techwriter/research` — AI research + draft per section (caps rag_chunks to 5, kg_entities to 10)
- `POST /api/techwriter/diagram` — generate Mermaid syntax from natural-language description

Constants in `tools/document_intelligence/constants.py`:
- `TEMPLATE_TYPES` — 6 types: STANDARD_GUIDE, SOP, RUNBOOK, ARCH_NETWORK, ARCH_APPLICATION, ARCH_SYSTEM
- `WRITEGUARD_MODES` — mode keys; `TEMPLATE_TYPE_TO_WRITEGUARD_MODE` maps each template type to its mode

Frontend:
- `tools/dashboard/static/js/dic-techwriter-sidebar.js` — `DICTechWriterSidebar.init({sidebarId, mode, debounceMs:1500})`. MutationObserver catches dynamically created `textarea[data-section-id]` elements. Debounced 1500ms → POST `/api/writeguard/analyze` → severity-coloured findings + SVG donut score. Apply-fix button calls `/api/writeguard/rewrite`.
- `doc_detail.html` — conditional two-column layout + `<aside id="wg-sidebar">` + AI Research drawer + Mermaid `<dialog>` editor; all guarded by `{% if doc.template_type %}`.

Content modes in `tools/writing/content_modes.py`:
- `standard_guide` — checks AWS/Azure/GCP/Oracle coverage, References section
- `architecture_doc` — checks decision log, security section, warns if no `[DIAGRAM:]` marker
- `sop_runbook` — checks numbered steps, Rollback, Prerequisites, Verification; suppresses tone+clichés
