# Document Intelligence Canvas (DIC) — Tools

The 20th ICDEV canvas: its own RAG+KG over documents, grounded NO-LLM search
with citations, freshness tracking, HITL + AI-labeled generation, and
RBAC+ABAC+RLS access control.

## Ingestion

| Tool | Purpose |
|------|---------|
| Page Geometry | tools/document_intelligence/page_geometry.py | dwr-fid-02. Word BOXES for a PDF (pdfplumber `extract_words(use_text_flow=True)`) and paragraph/run structure for a DOCX (python-docx) — the coordinate space `extract_text()` cannot produce, so a left pane can look like the document. THREE tables, two stories never merged: `dic_page_words` (box per word), `dic_doc_runs` (no `page` column — OOXML has no pages until rendered), `dic_document_geometry` (one row per doc: which story, the STATUS — an empty word list is seven different things — and the bound that was hit). NEVER pymupdf (undeclared, AGPL; pinned by AST test). `char_start`/`char_end` index the document's OWN stored text or are NULL, never 0; the rate is None when alignment never ran. Bounded (50 pages / 50k words, env-overridable), skippable (`ICDEV_DIC_WORD_GEOMETRY=0`), and both are REPORTED. Captured at ingest while the file is still on disk; `--backfill` re-reads dwr-fid-01's retained originals | `--survey`, `--doc`, `--page`, `--backfill`, `--limits`, `--json` | GeometryResult / rows / survey |
| Chunk Repair | tools/document_intelligence/chunk_repair.py | oss-hitl-01. HITL merge/split/re-chunk/re-embed for rag_chunks — chunks were READ-ONLY, a dead end for a grounded-citation canvas. Every repair is AUDITED and re-baselines dic_chunk_links.chunk_hash onto the new chunk (migration 267's evidence baseline), so drift detection does not misfire on a deliberately-fixed chunk. Re-embed degrades to 'embedding pending' when the provider is down. Reviewer-gated via the existing section-review RBAC; POST /api/chunks/<collection>/repair | (import) + HTTP | RepairResult (new ids, hashes, links_rebaselined) |
| Table Extraction | tools/document_intelligence/table_extract.py | oss-table-01. Recovers real tables from PDFs via pdfplumber `extract_tables()` (never called anywhere before) and renders them as GitHub-flavoured markdown, so a chunk keeps each cell associated with its column instead of the interleaved reading order `extract_text()` produces. `table_support()` is a runtime capability PROBE, not a docstring claim. Appends to `_extract_pdf` additively — a detection regression cannot cost text that already extracted, and a crash in the table engine cannot fail the document | `--probe`, `--json`, `--max-pages` | ExtractedTable[] / markdown |
| `tools/document_intelligence/extractors.py` | Built-in air-gap-safe file extractors. Returns `Extraction(text, provider, content_type, page_count, title, metadata, warnings)`. Supports PDF (pypdf), DOCX (python-docx), XLSX (openpyxl), PPTX (python-pptx), PNG (pytesseract/easyocr), HTML (strip-html), TXT (plain read). All formats degrade gracefully — missing library yields `text=""` + warning rather than raising. Called by `ingest_orchestrator.py` before chunking. MarkItDown is tried first for DOCX/PPTX/XLSX/images/audio when installed (see converter below). |
| `tools/document_intelligence/converters/markitdown_adapter.py` | Optional enhanced extractor wrapping Microsoft MarkItDown (pip install markitdown). Converts DOCX/PPTX/XLSX/PDF/HTML/images/audio to structured Markdown with header/table preservation. Gracefully degrades to `Extraction(text="", provider="markitdown-unavailable")` when library is absent. `should_use_markitdown(ext)` guards the dispatch in `extract_file()`. `SUPPORTED_EXTENSIONS` frozenset lists all handled formats; `_PREFER_BUILTIN` excludes .txt/.md/.py (adapt-md-02/03). |
| `tools/document_intelligence/collection_registry.py` | `ensure_collection(conn, collection_id, *, name, tenant_id, classification)` — get-or-create the `dic_collections` row before a document is written. `dic_documents.collection_id` is free-text with no FK and every ingest path takes it from the caller (`/api/ingest` defaults to `"default"`, the CLI passes `--collection` verbatim, the IDR flow mints `idr-<session_id>`); the Collections UI enumerates `dic_collections`, so a document whose collection has no row is ingested, chunked, embedded, scanned — and unreachable. Call it before any `dic_documents` INSERT. Does not commit (caller owns the transaction) and does not swallow errors (a swallowed failure poisons the PG transaction and resurfaces on the document INSERT). Returns False for an empty id rather than inventing one. `most_restrictive(*classifications)` ranks markings explicitly — classification does NOT sort alphabetically, so `MAX()` over raw text would rank `UNCLASSIFIED` above `SECRET`. Repair for existing rows: migration 268. |
| `tools/document_intelligence/ingest_orchestrator.py` | Route a file → provider (by extension) → extract → REUSE `icdev.tools.rag.chunker.chunk_content` + `IngestionManager.ingest_source` to chunk/embed/upsert into the vector store → bridge each chunk into the KG via `rag_to_kg_ingester.ingest_chunk_to_kg` → write `dic_documents` + initial `dic_versions(origin='human_authored', status='approved')` + `dic_chunk_links` (rag chunk → doc + page/section). Stamps `tenant_id`/`classification` from the caller's security context on every row. |
| `tools/document_intelligence/author_evidence.py` | dwr-ev-01. Author-supplied content as a DECLARED currency source. `parse_assertions(form_value)` validates the `author_assertions` JSON an upload carries (refused whole on any malformed entry, 400 at `/api/ingest`); `record_assertions(conn, doc_id=, assertions=, ...)` writes one `dic_author_assertions` row per (document, entity, version) in the ingest's own transaction and refreshes the `entity_currency` store for the ONE source id `dic_author_assertions` through `entity_currency.backfill(sources=[...])` — the same mapping the nightly sweep runs, never a second writer. `as_of` is the AUTHOR's clock (`as_of_basis: author_stated \| upload_time` says whether it was stated or defaulted); `created_at`/`observed_at` are ours. NO precedence logic here: args/entity_currency.yaml declares the source with `precedence: 0` and `resolve()` ranks it top while keeping every disagreeing source under `others` with `conflict: true`. `list_assertions(doc_id=, entity_key=)` serves the statements AS MADE for the `icdev_author_evidence` DataBridge connector; the drafter reads the RANKED answer through `cortex.resolve`. No LLM anywhere. DDL is the one copy (migration 20260908003920 imports it). |
| `tools/document_intelligence/sme_evidence.py` | dwr-ev-02. A review comment PROMOTED to an attributed SME assertion. A comment is an INSTRUCTION by default and is cited by nothing: `dic_section_annotations` is declared as a currency source nowhere and read by no evidence seam, so an unpromoted comment is ABSENT from the chain rather than weakly weighted. `promote_comment(conn, ann_id=, claim=, promoted_by=)` promotes exactly ONE comment (no bulk door, no heuristic, refuses a second promotion of the same comment with `AlreadyPromoted`), writes one `dic_sme_assertions` row and refreshes the store for the ONE source id `dic_sme_assertions` through `entity_currency.backfill(sources=[...])` — never a second writer of currency rows. THE PROSE IS NEVER PARSED: the CLAIM is typed by the promoting human and validated by `author_evidence.normalize_assertion` (one vocabulary for uploads and promotions), while the comment travels VERBATIM as the quotation — an assertion extracted from prose is a `text_pattern` claim and can never reach a pack (TRUST rule 2), pinned by an AST test. TWO CLOCKS: `as_of` is the SME's (`as_of_basis: sme_stated \| comment_time`), `promoted_at` is ours; `asserted_by` is COPIED off the comment, never typed by the promoter. Two SMEs disagreeing are TWO ROWS (the unique key is the comment) and the contradiction is REPORTED under `contradicts`. `promotions_for(conn, ann_ids)` is what the comments API attaches so the page renders a promoted comment apart from an instruction. No LLM anywhere. DDL is the one copy (migration 20260908071149 imports it). NOT reversible through this seam — `entity_currency.backfill` is upsert-only, so a delete here would strand the derived row; a later attributed statement supersedes an earlier one instead. |
| `python -m tools.document_intelligence` | Headless CLI: `--ingest <path> --collection <id> [--tenant ID] [--classification C] [--created-by U] [--no-embed] [--no-kg] [--json]`. |
| `tools/document_intelligence/originals.py` | dwr-fid-01: retain the UPLOADED original of a DIC document, content-addressed (`<root>/<sha256[:2]>/<sha256><suffix>`, atomic `os.replace`, dedup by content) BEFORE `/api/ingest` deletes its temp file, and record `original_path` / `original_sha256` / `original_retained_at` on `dic_documents` (migration 20260908003311). Root `ICDEV_DIC_ORIGINALS_DIR` (default `data/document_intelligence/originals`, git-ignored), kill switch `ICDEV_DIC_RETAIN_ORIGINALS=0` — off is REPORTED on the ingest result, never silent. `original_verdict(row)` says ONE of retained | retained_missing | retained_mismatch (`--verify`) | source_on_disk | absent (THE FINDING) | no_source (generated in-canvas, nothing to retain); the collection listing carries it as `original_status`, and a board that has not run the migration still lists (columns probed from the catalogue, never named blind). CLI: `python -m tools.document_intelligence.originals --survey [--json] [--verify]` / `--root`; `unmeasurable` over no documents; states `schema.original_columns_present` and bytes on disk. Measured 2026-09-07: 55 docs, 29 absent, 15 no_source, 11 source_on_disk. |

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
| `tools/document_intelligence/ssp_evidence.py` | Governed evidence seam for SSP-fragment drafting (cef-di-03). `resolve_evidence(control_id, frameworks=...)` replaces `acoic._retrieve_evidence`'s bare `RAGRetriever.search` with ONE `cortex.resolve(control_id)` call — the currency store, RAG, DIC, the KG and the KB under the 8-gate TRUST chain, one `cortex_audit` row and one `source_citation_registry` row per lookup. Returns `SSPEvidence(texts, citations, ...)` INDEX-ALIGNED, so a drafted `[SOURCE-N]` finally resolves to a source id / table / provenance id instead of a positional index into a list nobody persisted. `None` always means "use the legacy path" (toggle off, re-entrant, budget spent, Cortex absent); a governance refusal returns a bundle carrying `blocked`. Toggle: `cortex.enabled` in `args/dic_acoic_config.yaml`, DEFAULT OFF — off means the seam is never consulted. `fallback_on_empty` (default true) takes the legacy retrieval when the governed fan-out produced no drafting text. `reset_run_state()` / `run_stats()` are the per-run memo cache, outbound budget and cap report. `pack_evidence` citations are excluded — a pack's own verdict must never become the evidence for a control narrative. |
| `tools/document_intelligence/docgen_evidence.py` | Governed evidence seam for DIC document generation (cef-di-05). `resolve_evidence(query, collection_id=...)` replaces the bare `DICSearchEngine.search` in `doc_generator.generate_document` / `regenerate_section` — what the `POST /api/generate` and `POST /api/generate/section` routes call — with ONE `cortex.resolve(query)` call over the currency store, RAG, DIC, the KG and the KB under the 8-gate TRUST chain. Returns real `DICSearchResult` objects, so every consumer DOWNSTREAM of retrieval (the evidence blocks, the verifier replay, the persisted citations, the quality gate's `allowed_source_ids`) is untouched; `chunk_id` is the Cortex citation's `source_id`, so the existing `[source: chunk N]` contract holds without touching a prompt. `GovernedCitation` SUBCLASSES the DIC `Citation` and its `to_dict()` is a SUPERSET, so `citations_json` gains `source_type` / `source_table` / `provenance_id` / `evidence_path` and loses nothing. `screen_draft(text)` is the currency guard: it passes DRAFTED prose back through `resolve`, whose pack assessments are DETERMINISTIC (TRUST rule 1 — no LLM decides what is deprecated), and catches the one case `verifier.verify` structurally cannot, because a stale runbook SUPPORTS the claim that reintroduces a dead protocol. Returns `None` (never a clean screen) when it did not run. `None` from `resolve_evidence` always means "use the legacy path" (toggle off, re-entrant, budget spent, Cortex absent); a governance refusal returns a bundle carrying `blocked`. Toggle: `cortex.enabled` in `args/dic_docgen_config.yaml`, DEFAULT OFF — off means the seam is never consulted. `currency_guard.on_deprecated` is `annotate` (default) or `abstain`. `reset_run_state()` / `run_stats()` are the per-run memo cache, outbound budget and cap report. `pack_evidence` citations are excluded. NOT migrated, on purpose: the Chain-of-Debate paths (`ChainOrchestrator`, which consume an evidence STRING and work identically either way), the `"Source document content:"` query-scrape fallback, and `regenerate_section`'s `dic_sections` / `dic_versions` row reads (exact primary-key lookups, not evidence retrieval). |
| `tools/document_intelligence/search_evidence.py` | Governed evidence seam for DIC grounded search (cef-di-04). `resolve_evidence(query, collection_id=..., clearance=...)` replaces `DICSearchEngine._rag_search`'s bare `RAGRetriever.search` with ONE `cortex.resolve(query)` call — the currency store, RAG, DIC, the KG and the KB under the 8-gate TRUST chain, one `cortex_audit` row and one `source_citation_registry` row per lookup. Returns `SearchEvidence(candidates, citations, ...)` where `candidates` are in the shape `_rag_search` already returned, so the caller's citation packing, collection post-filter, clearance drop (still BEFORE the `top_k` cap) and attribution rerank all run unchanged. **The cycle:** Cortex's own `dic` rung IS `DICSearchEngine.search()`, and it arrives on a ThreadPoolExecutor WORKER thread, so the re-entrancy interlock is PROCESS-WIDE — a thread-local guard is structurally blind to the pool hop and would recurse in production. Rule: the innermost DIC search inside a resolve fan-out is always the raw rung. `None` always means "use the legacy path" (toggle off, re-entrant, collection-scoped, budget spent, Cortex absent) and every case is counted in `run_stats()`; a governance refusal returns a bundle carrying `blocked`. A collection-scoped ask DECLINES — `cortex.resolve` has no collection parameter, so a governed candidate carries no collection of record and the caller's post-filter would drop all of them. Toggle: `cortex.enabled` in `args/dic_search_config.yaml`, DEFAULT OFF. |
| `tools/document_intelligence/docdrift_evidence.py` | Read model behind the DocDrift currency panel (cef-ui-01). `attach_resolutions(drift_events)` bundles one view per finding for `/document-intelligence/docdrift`; `resolve_finding(entity, advisory=...)` calls the GOVERNED `cortex.resolve` and persists to `dic_docdrift_resolutions`; `resolve_findings(entities)` is the bounded batch (cap from `cortex.max_resolves_per_batch`, deferred entities returned NAMED in `skipped`). Keeps THREE axes apart, which is the whole point: `state` (current/deprecated/superseded/unknown/**not_resolved**/refused) reads the pack verdict ONLY, `evidence_health` (ok/degraded/failed/**unmeasured**) reads `backend_errors` ONLY — measured live, `TLS 1.1` is `superseded` with four of five backends timed out — and `advisory` (not_consulted/unavailable/no_opinion/opinion) carries the `sme` rung's OPINION, which is never evidence and never reaches a verdict. `not_resolved` and `unmeasured` exist so "we never asked" can never render as "we checked and it is fine". `unknown_reasons()` carries `no_pack_matched`/`no_evidence`/`backends_failed`/`packs_failed` through to the page with labels rather than flattening them. Toggle: `cortex.enabled` in `args/dic_docdrift_config.yaml` (DEFAULT ON — additive and read-only, unlike its sibling seams there is no legacy path to fall back to); `advisory.enabled` DEFAULT OFF (one LLM call), overridable per request unless `advisory.allow_request_override: false`. `run_stats()` reports resolutions/refusals/caps. |

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
| `tools/document_intelligence/search_engine.py` | DIC Grounded Search Engine. Default mode: BM25 + KG traversal (NO LLM, air-gap safe). Optional hybrid mode adds vector similarity + RRF fusion + cross-encoder rerank. Every result carries a mandatory citation pack; results with no traceable source are suppressed. `DICSearchEngine.search(query, collection_id, top_k, mode)` returns `list[DICSearchResult]` each with a `Citation` (doc_id, title, version_id, page, section, chunk_id). Falls back to pure SQL BM25 (`rag_chunks`) when the vector store is unavailable. **Governed candidates (cef-di-04):** `_rag_search` consults `search_evidence.resolve_evidence` first when `cortex.enabled` is on in `args/dic_search_config.yaml` (DEFAULT OFF); everything downstream — citation packing, the collection post-filter, the clearance drop BEFORE the `top_k` cap, the attribution rerank — is unchanged on both paths. `search()` IS Cortex's `dic` rung, so only `_rag_search` may consult the seam; the cycle is cut by a process-wide interlock in `search_evidence`. The ungoverned filesystem wiki Q&A cache (`_check_wiki_cache` / `_file_qa_to_wiki` / `_wiki_keyword_search`) was REMOVED — no tenant in its key, no clearance filter, no citations, no invalidation, and 0 files filed on the live deployment. |

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
| `tools/document_intelligence/freshness_engine.py` | DIC Freshness Engine — staleness scoring and autonomous reflex trigger. `scan_collection(collection_id, ...)` scores every document in a collection across five config-driven dimensions (`args/dic_freshness_config.yaml`): document age vs retention tier (default 90 days), time since last approved version, drift events since last update, pending-review section count, and (crx-kg-01) KG blast radius — how many recently-changed entities the doc cites (5th dimension, DEFAULT WEIGHT 0 so existing scores are unchanged until tuned; flagged docs route through the shared `acoic.handle_drift` sink). Writes per-doc `dic_doc_freshness` rows (upsert) and a collection-level `dic_freshness_scans` row; returns `ScanResult` with `stale_count`, `aging_count`, `fresh_count`, `regen_priority`. `corpus_heatmap(tenant_id, limit)` returns all documents ordered by score descending (stale first) for the dashboard heatmap. All reads use `get_connection()` so RLS applies. |
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
| `tools/document_intelligence/freshness_engine.py` | DIC Freshness Engine — staleness scoring and autonomous reflex trigger. `scan_collection(collection_id, *, tenant_id, classification, retention_days, changed_entities)` scores every document in a collection across five config-driven dimensions (`args/dic_freshness_config.yaml`): age vs retention tier, time since last approved version, drift events since last update, pending-review section count, and (crx-kg-01) KG blast radius (5th dimension, default weight 0 — inert until tuned). Returns `ScanResult` with per-doc `FreshnessResult` list (state ∈ `fresh`/`aging`/`stale`/`unknown`, composite score 0.0–1.0) and aggregate `stale_count`/`aging_count`/`fresh_count`/`regen_priority`. Persists per-doc rows to `dic_doc_freshness` (upsert) and a collection-level row to `dic_freshness_scans`. Air-gap safe — no LLM calls; pure date arithmetic + SQL. Feeds the `/document-intelligence/` heatmap and `dic_digest.py` weekly reflex. |

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
| `tools/document_intelligence/consistency_checker.py` | Cross-document concept overlap detector for propagating review flags when source content changes. `extract_changed_concepts(before, after)` returns new noun phrases (no-NLTK tokenizer, stop-word filtered, capped at 50 terms). `find_related_docs(doc_id, changed_concepts, tenant_id, limit)` walks `kg_nodes`/`kg_graphs` Python-side (avoids SQL JSON dialect issues) to find docs sharing concept nodes with the changed document. Returns `[{doc_id, doc_title, collection_id, last_updated, matching_concepts}]`. `find_docs_citing_changed_entities(changed_entities, *, min_overlap, tenant_id, limit)` (crx-kg-01) reuses the SAME concept-overlap traversal (shared `_docs_by_concept_overlap`) to compute the semantic *blast radius* of an entity change: docs citing N-or-more recently-changed KG entities, `[{doc_id, ..., matched_entities, overlap_count}]` sorted by overlap desc. All KG reads use `get_connection()` so RLS applies. |
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
| `tools/document_intelligence/tech_writing_assist.py` | AI research + drafting + diagram generation for the Tech Writer workspace. `research_and_draft(query, section_heading, template_type, ...)` → `ResearchResult`; `generate_diagram_syntax(description, diagram_type, template_type)` → `DiagramResult`. Never raises — all errors surface in result.error. Uses module-level optional imports (RAGRetriever, kg_retrieve, LLMRouter, fetch_content, is_airgap) so tests can patch them. Air-gap aware (skips web when air-gapped). `validate_standards_references(text)` — deterministic whitelist check (args/tw_standards_whitelist.yaml) of NIST SP 800-*, CMMC, FedRAMP, DISA SRG/STIG citations in the draft's References section; warnings land in `ResearchResult.warnings` and the WriteGuard sidebar. ARCH_* drafts route through `ChainOrchestrator.invoke_chain_of_debate` with single-shot fallback when `ICDEV_TW_COD_ENABLED=true` (default off). **cef-di-02:** `collection_id` now SCOPES retrieval on both chains and fails closed (`ResearchResult.scope` reports what it dropped) — it was accepted and ignored, which let a section draft cite any collection in the tenant. With `cortex.enabled: true` in `args/dic_techwriter.yaml` (default off) the retrieval half runs through the governed `cortex.resolve()` seam under the `document_intelligence` lens instead of hand-wiring RAGRetriever + graph_rag; `ResearchResult.retrieval_path` says which chain ran and `ResearchResult.resolution` carries the deterministic verdict/gaps/conflicts. |

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

## Export (rmf-wp-02)

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/exporter.py` | rmf-wp-02. DIC had NO export route. `export_version(version_id, fmt, *, force_*, force_reason, on_overrides)` runs the SAME placeholder + citation gates the approve route runs (`consistency_checker.check_version_consistency` / `check_version_citations`, the shared grounding primitives) and THEN WriteGuard (`run_full_quality_check`) over the assembled document -- docgen blocks publish on it, DIC never called it. Every gate fails CLOSED: a gate that could not measure is `unmeasured` and no `force_*` opens it; a measured defect needs the matching flag AND a non-empty reason, audited by the route BEFORE the file is written. Renders `md` / `html` (docgen's sanitised renderer) / `docx` (`rfi_docx_exporter.markdown_to_docx`, classification LABEL as the marking) / `pdf` (fpdf2 only) and INSERTs one `dic_artifacts` row per export (sha256, WriteGuard score, full gate report, `forced`, `version_status` at export time) -- migration 20260903194350. Library only; the route is `GET /document-intelligence/api/versions/<id>/export/<fmt>`, plus `/api/versions/<id>/artifacts` and `/api/artifacts/<id>/download`. Artifact dir: `ICDEV_DIC_ARTIFACT_DIR` (default `data/document_intelligence/artifacts`). | (import) + HTTP | `{artifact, gate}` / `ExportBlocked` / `ExportUnavailable` |
| `tools/db/migrations/20260907213944_dic_suggestions_anchor/up.py` | dwr-anchor-03: `dic_suggestions` gains the anchor columns (`anchor_section_id`, `anchor_start`, `anchor_end`, `anchor_text`, `anchor_basis` exact\|relocated\|unanchored, `origin_kind`, `applied_text`, `applied_by`). Python migration that probes the live catalogue and adds exactly the missing columns; `suggestion_store.resolve_anchor()` / `whole_section_anchor()` / `validate_anchor()` / `record_application()` are the store-side seams. |

## Threaded, anchored, resolvable comments (dwr-cmt-01)

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/annotation_store.py` | dwr-cmt-01. `dic_section_annotations` had `selected_text`, `category`, `author` and an open/resolved lifecycle -- and, measured on the live PG board 2026-09-08, **14 columns, ZERO rows and NO MIGRATION**: it was a runtime `CREATE TABLE IF NOT EXISTS` inside `blueprint.py`, so a deployment where that `_ensure` had run was indistinguishable from one where it had not. This store owns the schema, the anchor rule and the thread rules. THE ANCHOR IS THE SUGGESTION'S ANCHOR: `resolve_anchor` / `validate_anchor` are IMPORTED from `suggestion_store` (dwr-anchor-03) and never re-derived -- an AST test refuses a local copy, because two copies is how one surface starts disagreeing with another about whether a document moved. ORPHANED IS A READ-TIME VERDICT AND NEVER A COLUMN: a stored verdict about whether a span still matches goes stale on the one event it exists to describe, so `anchor_state(section_text, row)` re-slices the LIVE section on every read -- `verified` \| `orphaned` \| `unanchored` (a section-level comment, not a defect) \| `unverifiable` (the section could not be read -- NOT a clean bill of health, and never folded into the other two). An orphaned comment KEEPS its stored offsets; where the text is findable exactly once elsewhere that position is reported as an advisory `relocation_candidate` for a human and is NEVER written back, and an ambiguous match yields no candidate at all. THREADS ARE FLAT AND THE ROOT OWNS THE LIFECYCLE: a reply carries no anchor and no status of its own, a reply to a reply is refused, resolving writes the ROOT only, and deleting a root takes its replies with it and says how many. `anchor_from_selection` is the door for a surface holding selected TEXT and not offsets (`doc_detail` renders markdown, so a DOM offset indexes the rendered HTML) -- found once is `relocated`, an honest `str.find` guess recorded as one. | (import) | `create_annotation` / `list_threads` / `resolve_thread` / `reopen_thread` / `delete_thread` / `anchor_state` |
| `tools/db/migrations/20260908065203_dic_section_annotations_threads_and_anchors/up.py` | dwr-cmt-01: the table's FIRST migration. Adds `parent_ann_id`, `anchor_start`, `anchor_end`, `anchor_text`, `anchor_basis`, `tenant_id` -- all NULLABLE with no default, so NULL reads as NOT RECORDED. A Python migration for the same reason as 20260907213944: the table is created lazily and by nothing in `init_icdev_db.py`, so it faces three populations (live PG old shape, a SQLite db the blueprint touched, a database with no such table) and SQLite has no `ADD COLUMN IF NOT EXISTS`. There is deliberately NO `anchor_section_id` -- the row already carries `section_id` NOT NULL and that IS the section the offsets index. |

## Section derivation (dwr-sect-01)

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/section_deriver.py` | dwr-sect-01. `ingest_file` wrote `dic_documents`, `dic_versions` and `dic_chunk_links` and NEVER a `dic_sections` row -- sections were only ever created by `doc_generator`, import-from-docgen and template instantiate, so an INGESTED document rendered an empty Sections list and had no coordinate space for an anchored change to land in (measured on the live PG board 2026-09-07: **16 of 55 documents carried any section row at all**). `derive_sections(text, *, title)` splits a document along its own headings; `outline_for_document(..., max_sections)` adds a declared bound. Headings come from `tools/rag/breadcrumbs.parse_headings` (ATX + numbered clause) -- NOT a second parser, which would be a second opinion about what a heading is. THE ONE INVARIANT: `text[s.char_start:s.char_end] == s.content`, verbatim, because a tidied slice yields anchors that are off by however much was tidied and nothing downstream can detect it. CHUNKS ARE NOT SECTIONS and nothing re-chunks: `dic_chunk_links` stays the RETRIEVAL unit (overlapping, sized for embedding), a section is the EDITING and ANCHORING unit (non-overlapping, covers the text exactly once). THREE BASES, recorded on `dic_versions.section_basis` (migration 20260907215506, NULLABLE -- NULL means NOT RECORDED and must stay distinguishable from a measured verdict): `headings` | `whole_document` (no heading found) | `empty` (no text -- NO section is fabricated). A count cannot tell the first two apart, since a one-heading document and a fallback both yield exactly one section, and they lead to different fixes. Over the bound the outline degrades to `whole_document` and is NEVER truncated -- truncating drops document text to stay under a limit. Bound: `ICDEV_DIC_MAX_SECTIONS` (default 400). Ingested sections are written `origin='human_authored' status='approved'`, never the column defaults (`ai_generated`/`draft`), which would put real source prose into the AI-content population the citation and export gates are scoped to. Derivation never raises: a failure degrades to `empty` rather than losing an ingest that already extracted, chunked and embedded. | (import) | `DerivedOutline{sections, basis, heading_count}` |

## Degraded render honesty + ingest posture (dwr-fid-03)

| Tool | Purpose |
|------|---------|
| `tools/document_intelligence/reading_pane.py` | dwr-fid-03. `doc_detail` rendered `dic_sections` and, when there were none, ONE line -- *"No sections yet. Generate a draft or instantiate a template."* -- so a thin render was indistinguishable from a complete one. MEASURED on the live PG board 2026-09-08: 55 documents, 16 with sections, and the 39 without are NOT one population: **19** have text recoverable from `rag_chunks` (the degraded reading pane), **9** carry chunk links where EVERY one dangles (the document WAS chunked; a reindex / retention sweep / `delete_source` has since removed the rows), and **11** were never chunked at all. `build_pane(conn, doc_id, doc_row=)` returns a `ReadingPane` whose `basis` is one of `sections` (the full render owns it; this module declines) \| `chunks` \| `chunks_missing` \| `no_text` \| `unmeasurable` -- three different fixes, so three different panes, and `chunks_missing` is never folded into `no_text`. TWO DERIVATIONS FOR THE TEXT, and WHICH ONE ANSWERED IS RECORDED as `text_source`: the document's own `dic_chunk_links` first (`chunk_links` -- resolves for 2 documents live), then `rag_chunks` by `source_type`/`source_table`/`source_id` (`rag_source_id` -- 17 more); a silent fallback would hide that the link table has gone stale, so the pane prints "0 of N of this document's own links resolve". THE PANE STATES ITS OWN LIMITS: `limits` is a derived list of typed findings, each naming the fix, and the ORIGINAL half is `originals.original_verdict` IMPORTED (dwr-fid-01) and never re-derived -- an AST test refuses a local copy. An unmigrated board reads `original_unmeasurable`, NEVER `original_absent`: reporting 43 documents as having lost their upload when the column does not exist is a fabricated finding the size of the real one. ANCHORS: every rendered chunk carries a stable `anchor_id`, so `#chunk-<id>` is deep-linkable and survives a re-render -- that is the PANE's coordinate space and NOT the REVIEW one, and the pane says so, because comments and suggestions index offsets into a section's content and this document has none. `_safe_rows` turns a broken query into an EMPTY LIST, which is exactly how a missing table reads as a document with no chunks, so every read here reports its own failure and a failed read is `unmeasurable` -- never a clean bill of health. | `python -m tools.document_intelligence.reading_pane --survey [--json]` / `--doc <doc_id>` | `ReadingPane{basis, text_source, chunks, limits, links_total, links_resolved}` |
| `tools/document_intelligence/ingest_guard.py` | dwr-fid-03 / sandbox-coverage **Gap 69**. `POST /document-intelligence/api/ingest` accepts an ARBITRARY uploaded file with NO extension allowlist and NO per-route size cap (the only limit is the platform-wide `MAX_CONTENT_LENGTH`) and hands it to pypdf / pymupdf / pdfplumber / pypdfium2 / python-docx / openpyxl / python-pptx / Pillow / easyocr / pytesseract (which spawns the external `tesseract` binary), plus MarkItDown's `.zip` / `.msg` / `.eml` / audio expansion where it is installed. It had no entry in `docs/security/sandbox-coverage.md`, and the `sandbox_coverage` coherence check asserts only that the document exists and is linked, so nothing would ever have caught that. Decision: **sandboxed-on-demand**, the Gap 3 / Gap 25 class and its widest member. `evaluate_upload(filename, content_length=, strict=)` is what makes the posture more than a sentence: on `ICDEV_STRICT_SANDBOX=1` a format that reaches a native parser is REFUSED at the route with a `415` and a stated reason. IT IS A REFUSAL AND NOT ISOLATION, and says so everywhere it is reported -- DIC extraction is not routed through `SandboxExecutor`, and declaring a posture nothing consults is the declared-but-never-consumed defect one layer up from the code it purports to govern. The strict switch is `tools.analyzers.sandbox.strict_sandbox_enabled` IMPORTED, never a second reading of the env var (AST-pinned). THE PERMITTED CLASS IS DERIVED FROM `extractors._EXTRACTORS`: an extension is `text` only because the registry maps it to `_extract_text`, so a binary format added there cannot join the permitted class by being forgotten in a second list. An UNKNOWN extension is `text` and correctly so -- `extract_file`'s fallback is `Path.read_text`, so a `.pdf` renamed `.foo` never reaches a parser; the classification is of the CODE PATH. `.zip`/`.msg`/`.eml`/`.epub`/`.xls` are classed `native` on their WORST case rather than on what happens to be installed. Default posture is UNCHANGED (`ICDEV_STRICT_SANDBOX` is unset everywhere this ships), so it refuses nothing today and owes no fire-rate survey. | (import) | `{allowed, reason, posture, extension, parser_class, size_cap}` |
## Redraft with my comments (dwr-ev-03)

| Tool | Purpose | Entry | Returns |
|------|---------|-------|---------|
| `tools/document_intelligence/redraft.py` | dwr-ev-03. A per-change action a HUMAN presses: `redraft_change(suggestion_id, actor, ...)` re-runs the UNCHANGED TRUST gate chain (`redline_drafter.draft_redline`) with the change's comment thread as editing INSTRUCTIONS and the governed author/SME currency evidence in the bundle, then SUPERSEDES the change it replaces. A comment never fires a redraft -- a resolution costs 10-12s against five backends, so a comment box that resolved on save would spend a run's budget on day one. INSTRUCTIONS ARE PROSE, NEVER EVIDENCE: they reach the model in the user prompt and nowhere else, so a comment saying "cite [source: my-email]" hard-blocks at TRUST gate 1 and one naming another product hard-blocks at gate 2 -- asserted in both directions, with a control that the same draft citing a real id is NOT blocked. `extra_evidence` is the deliberately separate parameter that DOES widen `allowed_ids`, and its only source is `doc_modernization.evidence.resolve_evidence` -- no private SELECT on `dic_author_assertions` or `entity_currency` (dwr-ev-01's rule, pinned by an AST test over what is handed to a cursor). `gather_evidence()` keeps five zeroes apart in `evidence_basis`: `not_consulted` (cortex.enabled off -- THE SHIPPED DEFAULT, and never "no author evidence found") \| `capped` \| `blocked` \| `no_evidence` (the measurement) \| `resolved`; the WINNER AND THE DISAGREEING LOSERS both become citable, because handing the drafter only the winner restores the silent overwrite dwr-ev-01 prevents. `thread_for_change()` reads `annotation_store.list_threads` (dwr-cmt-01) -- the one reader of what a thread IS, so `status='open'` filters the THREAD and an open REPLY under a RESOLVED root is correctly not an instruction -- selecting by anchor overlap where the change and the roots carry spans and by section otherwise, RECORDING which (`section_scope` \| `anchor_overlap` \| `no_section_of_record` -- an unanswerable question, never an empty thread). Bounds are reported, never silent: `max_resolves_per_run` (default 3, one run = one press) defers entities BY NAME in `evidence.deferred`, `max_instructions` (8) defers the OLDEST comments by ann_id, `instruction_char_cap` flags `truncated` per comment. `REFUSALS` is a CLOSED mapping and every refusal is named in the response and the audit row -- a 200 over a no-op is the defect dwr-anchor-05 fixes one table over. Audit `dic.redraft`, phase in `action`: `.intent` BEFORE the act, fail-closed (no row, no redraft; migration 20260908071433), `.drafted`/`.refused` after, best-effort. Supersede runs AFTER the new draft exists (through dwr-anchor-05's one door), so a blocked draft can never destroy a good change, and `superseded` is reported False rather than assumed. Config: `args/dic_redraft_config.yaml`. Route: `POST /document-intelligence/api/suggestions/<id>/redraft` (editor role; 409 + `refusal` for every refusal). | (import) + HTTP | `RedraftResult{status, refusal, detail, new_suggestion_id, superseded, thread, evidence, bounds, audited}` |
| `tools/db/migrations/20260908071432_dic_suggestions_supersede_columns/up.py` | dwr-ev-03: `dic_suggestions` gains `successor_suggestion_id` -- the change that REPLACED this one, extending dwr-anchor-05's `supersede_suggestion` rather than forking it. NULLABLE because an anchor-stale supersede has no successor, and NULL means NOT RECORDED. Not named `superseded_by`: that parameter already names the MECHANISM (it lands in `dic_suggestion_decisions.decided_by`), and two things under one name is how a reader comes to believe an id is an actor. The decision row is still written on dwr-anchor-05's terms, so a retirement can never read as a human's accept-or-reject. Python migration probing the live catalogue, `NEW_COLUMNS` pinned by test to `suggestion_store.SUPERSEDE_COLUMNS`. |
| `tools/db/migrations/20260908071433_dic_redraft_audit_event_type/up.py` | dwr-ev-03: rebuilds `audit_trail`'s event_type CHECK from `VALID_EVENT_TYPES` to admit `dic.redraft`. Until it runs, every redraft on an existing PostgreSQL database is refused `unaudited_refused` -- fail-closed by design, the correct reading and not an obstacle. |
| `review_rail.py` | dwr-cmt-02 | One review rail: comment threads and change cards for a document in ONE stream, ordered by anchor position. Position is a MEASUREMENT — only a verified anchor yields one; `unplaced` (names no section) is never folded into `unpositioned`. Library + `GET /document-intelligence/api/documents/<id>/review-rail`. |
| `tools/document_intelligence/word_diff.py` | dwr-ws-01. WORD-level diff spans over one before/after pair. `tokenize(text)` splits on word boundaries KEEPING whitespace and punctuation (`\w+` \| `\s+` \| `[^\w\s]` -- exhaustive and disjoint over any str), so `"".join(tokenize(t)) == t` for every input; `word_opcodes(before, after)` returns `[{tag: equal\|insert\|delete, text}]`, one span per opcode with a `replace` decomposed into delete-THEN-insert; `diff_words(before, after)` is the whole answer (`spans`, `round_trip`, `added_words`, `removed_words`, `changed`). THE ROUND TRIP IS THE INVARIANT: equal+delete reassemble `before` and equal+insert reassemble `after`, byte for byte -- a diff renderer that silently drops a character is worse than none, so `diff_words` returns `spans: None` (never a truncated best effort) when `round_trip` fails. `count_words` counts `\w+` tokens only, so a 3-word insertion does not report 11 because it carried spaces and a full stop; a punctuation-only change counts 0 and is still visible in the spans. `autojunk=False` -- with it on, every space and every `the` becomes junk past 200 tokens and the diff degrades on exactly the long sections that need it. stdlib `difflib` + `re` ONLY: nothing is vendored, which the air-gap posture requires. | (import) | `list[{tag,text}]` / `dict` |
| `tools/document_intelligence/change_set.py` | dwr-ws-01. Assembles the change set behind `GET /document-intelligence/api/change-set`: one word-level diff per proposal plus what the system already knows about it. `before_side()` distinguishes THE ADDRESSABLE SPAN (`anchor_text`, when `anchor_basis` is exact\|relocated -- what the accept path would splice, including an empty `0:0` insertion anchor) from a drafter's own before-text on an UNANCHORED row (`current_content`, a real preview that is NOT appliable); `after_side()` lets `applied_text` outrank the draft, because on edit-then-accept that is what shipped. `change_view()` is pure and reads -- never re-derives -- `rationale` (dic_suggestions), `currency_verdict`/`severity`/`confidence` (docmod_findings via `redline_suggestion_id`), the band from `citation_grounding.classify_confidence` applied to the STORED score, the inline citations from `citation_grounding.parse_citations`, and `evidence_health`/`evidence_state`/`evidence_verdict` from `dic_docdrift_resolutions` via `docdrift_evidence.latest_resolutions`. Every field names the row it came from and a field that could not be read is `None` with a `None` source, never a default verdict. THE TWO CURRENCY DERIVATIONS ARE KEPT APART (`currency_verdict` from the scan, `evidence_verdict` from the resolution) -- a change set adjudicates nothing. An entity with no resolution row reads `unmeasured`/`not_resolved`, NEVER `ok`: measured 2026-09-08, 4 of the 10 entity labels carrying a drafted redline (SNMPv2c, telnet, Windows Server 2012 R2, CentOS 7) have none. `anchor_verified` is True\|False\|**None** -- None is "nobody looked", and it is only re-derived through `suggestion_store.verify_anchor`, the same function the accept path asks. FOUR run states: `unmeasured` (store unreadable -- not a clean board) \| `no_suggestions` \| `none_anchored` (proposals exist, NOT ONE addressable -- the live board: 58 of 58 carry `anchor_basis` NULL, so a set restricted to anchored rows would return an empty list reading as "nothing proposed") \| `changes`. Reads; writes nothing, decides nothing, applies nothing. | (import) | `dict{state, changes[], counts, truncated}` |
