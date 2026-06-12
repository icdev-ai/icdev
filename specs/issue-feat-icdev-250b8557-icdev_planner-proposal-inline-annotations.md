# CUI // SP-CTI
# Feature: Proposal Inline Annotation with Category Tagging

## Metadata
run_id: `250b8557`

## Feature Description
Users reviewing a proposal section's draft content need the ability to select any
text span, attach a comment, and tag it with a structured category. Annotations
persist, display as color-coded margin notes alongside the draft, and can be filtered
by category. This enables structured review workflows (Shipley Pink/Red/Gold) and
captures evaluation-team feedback in a queryable, auditable format.

## User Story
As a proposal reviewer
I want to highlight text in a draft and attach a categorized comment
So that the proposal team can track and address specific feedback inline

## Solution Statement
Add a lightweight annotation layer to the Draft Content tab of the proposal section
detail page. Text selection triggers a popup with a comment field and category picker.
Annotations are saved to a new `proposal_section_annotations` DB table and rendered as
color-coded margin notes in a right-side panel. A filter bar allows showing/hiding
annotations by category.

## COA Context
No COA selected — using full requirements.

## ATO Impact Assessment
- **Boundary Impact**: GREEN — no new external connections, data stays in existing DB
- **New NIST Controls**: AU-2 (new annotation audit events), AC-3 (annotation CRUD respects existing MAC checks)
- **SSP Impact**: none — additive capability within existing boundary
- **Data Classification Change**: no

## Relevant Files
- `tools/dashboard/templates/proposals/section_detail.html` — Draft Content tab, annotation tab, JS
- `tools/dashboard/api/proposals.py` — new annotation CRUD endpoints
- `tools/dashboard/static/js/proposals.js` — annotation functions, export
- `tests/test_proposals_annotations.py` — NEW: unit + integration tests
- `tests/conftest.py` — add `proposal_section_annotations` to MINIMAL_ICDEV_SCHEMA

### New Files
- `tests/test_proposals_annotations.py`

## Implementation Plan

### Phase 1: Foundation — DB + API
1. Add `_ensure_annotations_table(conn)` helper in proposals.py
2. Add 4 endpoints: GET/POST `/sections/<id>/annotations`, PUT/DELETE `/annotations/<id>`
3. Add table schema to conftest.py MINIMAL_ICDEV_SCHEMA

### Phase 2: Core — JS + Template
4. Add annotation JS (selection handler, popup, render highlights, filter) inline to section_detail.html
5. Export annotation functions through ICDEV.proposals in proposals.js
6. Add "Annotations" tab button with dynamic count
7. Split Draft Content tab into draft column + margin notes panel
8. Add annotation popup markup
9. Add filter bar with category chips

### Phase 3: Tests
10. Write pytest tests for all 4 API endpoints

## Step by Step Tasks

### Task 1: DB helper + schema
- Add `_ensure_annotations_table(conn)` to proposals.py (pattern matches existing `_ensure_blackhat_table`)
- Schema: id, section_id, draft_id, selected_text, category, comment, author, status (open/resolved), classification, created_at, updated_at
- Category CHECK: question|improvement|compliance|strength|weakness|risk|editorial

### Task 2: API endpoints
- `GET  /api/proposals/sections/<sec_id>/annotations` — list, optional `?status=open`
- `POST /api/proposals/sections/<sec_id>/annotations` — create {selected_text, category, comment, author, draft_id}
- `PUT  /api/proposals/annotations/<ann_id>` — update {comment, category, status, resolution_note}
- `DELETE /api/proposals/annotations/<ann_id>` — delete

### Task 3: Tests (TDD — write first)
- test_create_annotation_returns_201
- test_list_annotations_for_section
- test_update_annotation_status_to_resolved
- test_delete_annotation
- test_create_annotation_invalid_category_returns_400
- test_list_annotations_filter_by_status

### Task 4: Template — Annotations tab + Draft Content split view
- New tab button: `<button data-tab="stab-annotations">Annotations (<span id="ann-count">0</span>)</button>`
- Dedicated Annotations tab: filter chips + annotation cards list
- Draft Content tab: split layout — draft div (left, ~72%) + margin panel (right, ~28%)
- Annotation popup markup (hidden, positioned via JS)

### Task 5: Inline annotation JS (in section_detail.html)
- `annotationMode` toggle (button in draft toolbar)
- `mouseup` handler on `#draft-rendered` — capture selection, show popup
- `saveAnnotation()` — POST to API, re-render highlights, refresh margin panel
- `renderHighlights(annotations)` — scan rendered HTML, wrap matching text in `<mark>`
- `renderMarginNotes(annotations)` — build annotation cards in right panel
- `filterAnnotations(category)` — show/hide cards + highlights by category
- `resolveAnnotation(id)` — PUT status=resolved, re-render
- `deleteAnnotation(id)` — DELETE, re-render
- Load annotations on tab activate

### Task 6: Export in proposals.js
- Add `loadAnnotations`, `saveAnnotation`, `resolveAnnotation`, `deleteAnnotation` to ICDEV.proposals export

## Testing Strategy
### Unit Tests
- Each of the 6 test cases in test_proposals_annotations.py
- Use Flask test client with in-memory SQLite (matches conftest pattern)

### Edge Cases
- Empty selection (no text selected) — popup should not appear
- Duplicate text in draft — first occurrence highlighted
- Resolved annotations shown with strikethrough in margin panel
- No draft exists — annotation tab shows placeholder message
- Category validation server-side (invalid category → 400)

## Acceptance Criteria
- [ ] User can select text in Draft Content tab and add a categorized comment
- [ ] Annotations display as color-coded highlights in the draft and margin notes in right panel
- [ ] Filter chips show/hide annotations by category
- [ ] Annotations persist across page reloads (stored in DB)
- [ ] Annotations tab shows count of open annotations
- [ ] Resolved annotations visually distinguished (muted / strikethrough)
- [ ] All 6 pytest tests pass
- [ ] Ruff: 0 violations on new/modified files
- [ ] WriteGuard audit event logged for annotation create/resolve

## NIST 800-53 Controls
- AU-2: annotation create/resolve events logged to audit_trail
- AC-3: endpoints use existing `_get_db()` which enforces RLS/MAC
- SI-12: annotations are not append-only (reviewers can resolve/delete)

## Notes
- Use `_ensure_annotations_table(conn)` lazy-init pattern (not a migration) — matches `_ensure_blackhat_table` pattern already in proposals.py
- Do NOT add to APPEND_ONLY_TABLES — annotations are mutable (resolve/delete is core UX)
- Character offset approach skipped — use selected_text string matching for highlight rendering (simpler, robust for unique proposal phrases)

# CUI // SP-CTI
