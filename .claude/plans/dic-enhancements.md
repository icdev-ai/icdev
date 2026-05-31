# Plan: DIC Enhancements — Chunking Fix, Templates, Dedup, Collaboration, Section AI

> **Status:** Draft — pending user approval  
> **Scope:** Fixes 6 reported issues + closes gaps vs. original `dic-discovery.md` plan  
> **Estimated files touched:** ~12–15  
> **Risk:** Medium (multi-file backend + template changes; requires Playwright V&V)

---

## Problem Statement

From user observation and code audit:

1. **Upload completes with 0 chunks** — PDF/DOCX files fall through to a binary-as-text fallback because `tools.document_intelligence.providers` does not exist. The chunker receives near-empty text and returns 0 chunks. The user sees the file "complete" but nothing is stored.
2. **Templates page is read-only** — `templates.html` renders static cards with no actions. Users cannot instantiate a template.
3. **Dedup is partial / broken** — Chunk-level dedup exists via `store.get_by_content_hash()`, but document-level dedup is missing. Re-uploading the same file creates a new `dic_documents` row every time because `doc_id` is based on the temp file path, which changes per upload.
4. **Collaboration is table-only** — `dic_team_access` exists, but there is no section/document assignment, no "Revise" action, no role-based UI gating, and no reviewer workflow beyond Approve/Reject.
5. **AI assist is document-level only** — `doc_generator.py` drafts all sections from the same global evidence block. No per-section targeted retrieval. No "regenerate this section" button. Citations exist but are not shown inline in the UI.
6. **Gaps vs. original plan** — `dic-discovery.md` identifies 27 tasks; several wow-factors and baseline ECM features are missing: knowledge-handoff workflow, KG explorer, check-out/check-in locking, version diff, records retention, freshness heatmap.

---

## Architecture Decisions

### AD-1: Provider strategy for file extraction
**Decision:** Build a lightweight built-in extractor module `tools/document_intelligence/extractors.py` rather than wait for the optional `providers` package. Use libraries already in `requirements.txt` or stdlib where possible; gracefully degrade with clear error messages for unsupported formats.

- PDF → `PyPDF2` (read text) + `pdf2image` + `PaddleOCR` (if image PDF) — best-effort
- DOCX → `python-docx`
- XLSX/PPTX → `openpyxl` / `python-pptx` — extract text cells/slides
- Images → OCR via `PaddleOCR` or `pytesseract` if available; otherwise skip with warning
- HTML/TXT/MD → existing built-in (keep)

**Why:** The advertised upload widget lists PDF/DOCX/XLSX/PPTX/PNG/JPG/HTML. Users expect these to work. A missing optional package is a poor UX.

### AD-2: Document dedup key
**Decision:** Compute `content_sha256` before any DB write. Use `(content_sha256, collection_id)` as the dedup key. On duplicate, create a new `dic_versions` row linked to the existing `doc_id` rather than a new document row.

**Why:** File name and temp path are unstable; content hash is stable. This prevents duplicate documents while preserving version history.

### AD-3: Collaboration scope
**Decision:** Keep roles simple: `viewer`, `editor`, `reviewer`, `admin`. Assignment is at the **document version** level (`dic_versions.assigned_to`). Section-level assignment is out of scope for this iteration — the generate/regen flow operates on sections, but review gates are on versions.

**Why:** The user asked for "section or document assignment." Section-level assignment adds significant schema + UI complexity. Document-level assignment with per-section AI regeneration is the right 80/20.

### AD-4: Per-section AI regeneration
**Decision:** Add a new endpoint `POST /api/generate/section` that takes a `section_heading`, `doc_id`, `version_id`, and `collection_id`. It performs a **targeted DICSearchEngine query** using `section_heading + document title` as the query, drafts the section from the top 5 retrieved chunks, runs CoD verification, and writes a new `dic_versions` row with incremented `version_no` and `origin='ai_assisted'`.

**Why:** Context-aware means the evidence must be scoped to the section topic, not the whole document query. The verifier already exists; we reuse it.

### AD-5: Citations in generated text
**Decision:** Generated sections must include bracketed citations `[source: doc_title · p.N · chunk_id]`. The UI must render these as clickable citation chips linking back to the source chunk. The `doc_generator.py` already emits `[source: chunk {chunk_id}]` — we enhance this to include doc title and page.

**Why:** User explicitly said "MUST have citation to actual source." The current citation is a chunk ID only, which is not human-readable.

---

## Implementation Plan

### Phase 1: Ingestion Fix — Real File Extraction (P1)
**Files:** `tools/document_intelligence/extractors.py` (new), `tools/document_intelligence/ingest_orchestrator.py`

1. **Create `extractors.py`** with built-in extractors:
   - `PdfExtractor` — `PyPDF2` text extraction; if text is sparse (<100 chars), warn that OCR may be needed
   - `DocxExtractor` — `python-docx` paragraph text extraction
   - `XlsxExtractor` — `openpyxl` cell text extraction
   - `PptxExtractor` — `python-pptx` slide text extraction
   - `ImageExtractor` — best-effort OCR via `PaddleOCR` or `pytesseract`; skip gracefully if neither installed
   - `HtmlExtractor` — existing `_strip_html` logic, moved here
   - `TextExtractor` — existing logic, moved here
   - `ExtractorRegistry` — maps extensions → extractor class

2. **Update `ingest_orchestrator.py`**:
   - Replace `_select_extractor()` to use `extractors.ExtractorRegistry`
   - Keep `_try_provider_package()` as an override if the optional package ever lands
   - Log the extractor used and any warnings (e.g., "PDF text sparse — consider OCR")
   - If extraction yields empty text, return an error in `IngestOutcome.errors` and set `chunks=0`

3. **Add `content_sha256` dedup before DB write**:
   - After extraction, compute `content_sha256 = hashlib.sha256(text.encode()).hexdigest()`
   - Query `dic_documents` for `(content_sha256, collection_id)`
   - If found: skip chunking/embedding, create a new `dic_versions` row pointing to existing `doc_id`, set `chunks=0` in outcome but report "duplicate — linked to existing document"
   - If not found: proceed with normal ingest

4. **Add `SUPPORTED_EXTENSIONS` enforcement**:
   - Reject uploads with extensions not in `constants.SUPPORTED_EXTENSIONS` with clear error message

### Phase 2: Templates — Make Them Actionable (P1)
**Files:** `tools/dashboard/templates/document_intelligence/templates.html`, `tools/document_intelligence/blueprint.py`

1. **Update `templates.html`**:
   - Add "Start with this template" button on each card
   - Buttons link to `/document-intelligence/generate?template=<id>`
   - Add a "Preview parameters" expandable section per template showing expected inputs (query placeholder, collection recommendation)

2. **Update `blueprint.py` `generate()` route**:
   - Read `request.args.get("template")`
   - Pre-fill the generate form: if template is "acoic", default query = "ACOIC drift → impacted document regeneration"; if "sop-refresh", default query = "SOP refresh against current process"; etc.
   - Pass `preselected_template` to the template

3. **Update `generate.html`**:
   - If `preselected_template` is passed, auto-check the matching radio button
   - Pre-fill query input if provided

### Phase 3: Dedup — Document-Level + Cross-Collection (P2)
**Files:** `tools/document_intelligence/ingest_orchestrator.py`, `tools/document_intelligence/db/init_db.py`

1. **Add `dic_document_duplicates` table** (optional — can also use `dic_versions`):
   - `dup_id`, `original_doc_id`, `duplicate_doc_id`, `detected_at`, `match_type` (`content_hash`, `filename`), `tenant_id`
   - This is **append-only** — add to `APPEND_ONLY_TABLES` in `constants.py`

2. **Enhance dedup in `ingest_file()`**:
   - After extraction, before chunking: compute `content_sha256`
   - Query: `SELECT doc_id FROM dic_documents WHERE content_sha256 = ? AND collection_id = ?`
   - If match found:
     - Create `dic_versions` row linked to existing doc (new version_no = max+1)
     - Log duplicate detection
     - Return `IngestOutcome` with `doc_id = existing_doc_id`, `chunks = 0`, `errors = ["duplicate skipped"]`
   - Also check cross-collection by filename: if same filename but different hash, warn user in outcome

3. **Update `index.html` upload UI**:
   - Show duplicate warning in the upload results (e.g., "⚠ This file already exists in collection 'default' — linked as new version")

### Phase 4: Collaboration — Roles, Assignment, Revise (P2)
**Files:** `tools/document_intelligence/db/init_db.py`, `tools/document_intelligence/blueprint.py`, `tools/dashboard/templates/document_intelligence/review.html`, `tools/document_intelligence/constants.py`

1. **Schema additions**:
   - `dic_versions`: add `assigned_to TEXT`, `assigned_by TEXT`, `assigned_at TEXT`, `reviewer_notes TEXT`
   - `dic_review_notes` table: `note_id`, `version_id`/`fragment_id`, `reviewer`, `note_text`, `action` (`approve`/`reject`/`revise`), `created_at`, `tenant_id`
   - Add CHECK constraint on `role` in `dic_team_access`: `CHECK(role IN ('admin','reviewer','editor','viewer'))`

2. **API additions in `blueprint.py`**:
   - `POST /api/review/<id>/assign` — assign version/fragment to a reviewer
   - `POST /api/review/<id>/revise` — marks as `needs_revision`, adds note, creates new draft task
   - `GET /api/review/assigned` — list items assigned to current user
   - Enforce role checks: viewer → read-only; editor → can create/revise; reviewer → can approve/reject/revise; admin → all

3. **Update `review.html`**:
   - Add "Request Revision" button (yellow) between Approve and Reject
   - Show assignment badge: "Assigned to: reviewer_x"
   - Show reviewer notes thread per version/fragment
   - Role-gate buttons: hide Approve/Reject/Revise if user is viewer
   - Add "Assign to reviewer" dropdown for admins

4. **Update `collections.html`**:
   - Add team member management UI (current API exists but no UI wiring)
   - Show role badges next to members

### Phase 5: AI Assist Per Section — Targeted Retrieval + Regen (P2)
**Files:** `tools/document_intelligence/doc_generator.py`, `tools/document_intelligence/blueprint.py`, `tools/dashboard/templates/document_intelligence/generate.html`, `tools/dashboard/templates/document_intelligence/review.html`

1. **Update `doc_generator.py`**:
   - Add `regenerate_section(doc_id, version_id, section_heading, collection_id, tenant_id)` function:
     - Query DICSearchEngine with `section_heading + " " + doc_title` as query
     - Retrieve top 5 chunks
     - Build evidence block from these chunks
     - Call LLM with `_SECTION_PROMPT` scoped to this evidence
     - Run `verify()` on the draft
     - Return `GeneratedSection` with enhanced citations
   - Enhance `_SECTION_PROMPT` to require citations in format: `[source: {doc_title} · p.{page} · chunk {chunk_id}]`

2. **Add `blueprint.py` endpoint**:
   - `POST /api/generate/section` — accepts `{doc_id, version_id, section_heading, collection_id}`
   - Calls `regenerate_section()`
   - Writes new `dic_versions` row with `version_no += 1`, `origin='ai_assisted'`, `status='pending_review'`
   - Returns the regenerated section + new version_id

3. **Update `generate.html`**:
   - After draft renders, add a "🔄 Regenerate section" button per section card
   - Button calls `POST /api/generate/section` and replaces the section content inline
   - Show inline citation chips: clickable spans that open a tooltip with source doc + page
   - Add "View citations" expander per section showing all cited sources

4. **Update `review.html`**:
   - In the version review panel, show sections from the version
   - Add "Regenerate this section" button for reviewers with `editor`+ role
   - Show citation chips in the review panel

### Phase 6: Gap Closure — Discovery Plan Gaps (P3)
**Files:** Multiple — lower priority, can be deferred

1. **Knowledge-handoff workflow** (`dic-handoff-01`):
   - New page `/document-intelligence/handoff`
   - Interview agenda builder from analytics `single_source` findings
   - CoD-verified generation into a new collection
   - Out of scope for this plan; create a follow-up kanban task

2. **KG "buried bodies" explorer** (`dic-explore-01`):
   - New page `/document-intelligence/explorer`
   - Surface orphans, single-source entities, contradictions from analytics
   - Graph visualization (reuse sigma.js if available)
   - Out of scope for this plan; create a follow-up kanban task

3. **Version diff**:
   - Add `POST /api/versions/<id>/diff` — compare two version IDs, return unified diff of text
   - UI: show diff in review panel when reviewing a new version
   - Small addition, can include in Phase 4

4. **Records retention / legal hold**:
   - Add `dic_retention_schedules` and `dic_legal_holds` tables
   - Out of scope; follow-up task

5. **Freshness heatmap**:
   - Enhance `analytics.html` with a visual heatmap (colored grid) of document freshness across collections
   - Reuse existing `freshness_scans` data
   - Small addition, can include as Phase 4.5

---

## Files to Modify

| # | File | Change |
|---|------|--------|
| 1 | `tools/document_intelligence/extractors.py` | **New** — built-in PDF/DOCX/XLSX/PPTX/image extractors |
| 2 | `tools/document_intelligence/ingest_orchestrator.py` | Wire extractors, add content-hash dedup, improve error reporting |
| 3 | `tools/document_intelligence/db/init_db.py` | Add `assigned_to` etc. to `dic_versions`, add `dic_review_notes`, add `dic_document_duplicates` |
| 4 | `tools/document_intelligence/constants.py` | Add `dic_review_notes` and `dic_document_duplicates` to `APPEND_ONLY_TABLES` |
| 5 | `tools/document_intelligence/doc_generator.py` | Add `regenerate_section()`, enhance citation format, per-section targeted retrieval |
| 6 | `tools/document_intelligence/blueprint.py` | Add `/api/generate/section`, `/api/review/*/assign`, `/api/review/*/revise`, role checks, template prefill in generate route |
| 7 | `tools/dashboard/templates/document_intelligence/index.html` | Show duplicate warnings in upload results |
| 8 | `tools/dashboard/templates/document_intelligence/templates.html` | Add "Start with template" buttons, parameter previews |
| 9 | `tools/dashboard/templates/document_intelligence/generate.html` | Preselect template from query param, per-section regen buttons, citation chips |
| 10 | `tools/dashboard/templates/document_intelligence/review.html` | Add Revise button, assignment UI, reviewer notes thread, role gating, section view |
| 11 | `tools/dashboard/templates/document_intelligence/collections.html` | Team member management UI |
| 12 | `tests/test_dic_ingest_orchestrator.py` | Update tests for new extractor behavior and dedup |
| 13 | `docs/features/dic-discovery.md` | Mark completed gaps, add new follow-up tasks |

---

## Testing & V&V Plan

1. **Unit tests:**
   - `pytest tests/test_dic_ingest_orchestrator.py -v` — verify extraction + dedup
   - Add new test: `test_duplicate_file_skipped`
   - Add new test: `test_pdf_extraction_produces_chunks`

2. **Backend API tests:**
   - `pytest tests/ -k dic -v` — all DIC-related tests
   - Test `/api/generate/section` with mocked search engine
   - Test `/api/review/*/revise` and `/api/review/*/assign`

3. **Playwright E2E (mandatory per CLAUDE.md):**
   - Upload a PDF → verify chunks > 0 and doc appears in collections
   - Upload same file twice → verify duplicate warning
   - Click template "Start" → verify redirect to generate with prefill
   - Generate draft → verify sections render with citation chips
   - Click "Regenerate section" → verify new version created
   - Review page → verify Approve/Reject/Revise buttons, assignment dropdown

4. **Coherence:**
   - `python tools/workflow/coherence_checker.py --all --fix --gate`
   - `python tools/dx/companion.py --sync --write --json`

---

## Rollback / Safety

- All DB changes are additive (new columns, new tables) — no destructive migrations
- `dic_versions` new columns are nullable — existing rows remain valid
- New `APPEND_ONLY_TABLES` entries follow the existing append-only policy
- If extractors fail, the old fallback path still exists as last resort

---

## Success Criteria

- [ ] PDF upload produces >0 chunks (test with `ArtOfWar.pdf` or similar)
- [ ] DOCX upload extracts readable text and chunks
- [ ] Re-uploading same file shows duplicate warning and links as new version
- [ ] Templates page has clickable "Start with this template" buttons
- [ ] Generate page pre-fills query when template is selected
- [ ] Review page shows Approve / Reject / Revise buttons
- [ ] Review page supports assignment to specific reviewers
- [ ] Role-based gating hides admin/reviewer buttons from viewers
- [ ] Generated sections show inline citation chips with doc title + page
- [ ] "Regenerate section" button works and creates a new version
- [ ] Playwright E2E passes for upload → generate → review flow
- [ ] Coherence checker passes

---

## Open Questions for User

1. **OCR for scanned PDFs:** Do you have `PaddleOCR` / `pytesseract` installed, or should image-PDFs be rejected with a clear "scanned PDF — OCR not configured" message?
2. **User identity:** The review page currently hardcodes `reviewer: 'current_user'`. Should we integrate with the dashboard's existing auth system (`g.security_context.user_id`), or is a simple text entry field sufficient for now?
3. **Phase priority:** Should I implement all 5 phases in one session, or would you prefer to ship Phase 1+2 first (ingestion fix + templates) and iterate?
