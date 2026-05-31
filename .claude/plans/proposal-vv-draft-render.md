# Plan: Proposal V&V Pipeline + Draft Content Rendering

## Context
3 DHS solicitations now have 9 ICDEV-branded proposal section drafts in `proposal_section_drafts` (status=`draft`). We need to:
1. Run WriteGuard red-team review on all drafts, generating findings in the existing `proposal_reviews` + `proposal_review_findings` tables.
2. Render draft content on both the opportunity detail page (sections table) and section detail page (new tab).

---

## Part 1: WriteGuard V&V Pipeline

### New Tool: `tools/govcon/run_writeguard_on_drafts.py`

**Algorithm:**
1. Query DB for all `proposal_section_drafts` linked to the 3 target opportunity IDs where `status IN ('draft', 'reviewed')`.
2. For each draft:
   - Call `tools.pulse.writeguard.run_full_quality_check(draft['draft_content'])`
   - Create `proposal_reviews` row:
     - `review_type='red_team'`, `status='completed'`, `lead_reviewer='ICDEV WriteGuard'`, `overall_rating` derived from score
   - For each dimension with score < threshold, create `proposal_review_findings`:
     - `finding_type` = dimension name
     - `severity` = 'major' if score < 60, 'minor' if score < 75, else 'observation'
     - `description` = top 3 findings from that dimension
     - `recommendation` = WriteGuard recommendation
     - `status='open'`
   - Update `proposal_section_drafts`:
     - `status='reviewed'`, `reviewed_by='ICDEV WriteGuard'`, `reviewed_at=now`, `review_notes=JSON(summary)`
   - Update `proposal_sections.status` based on overall result:
     - overall_score >= 80 and no major findings → `approved`
     - overall_score >= 60 → `red_team_review` (or keep `gold_team_review` if already there)
     - overall_score < 60 or any major finding → `rework_red`
   - Append `proposal_status_history` for any section transition

**Why this approach:**
- FORGE-compliant: deterministic tool execution, no LLM in the tool
- Reuses existing `proposal_reviews` + `proposal_review_findings` schema (no migration)
- Reuses existing WriteGuard engine (`tools.pulse.writeguard`)
- Preserves append-only audit trail via status_history

### Alternatives considered:
- Async scheduler run: rejected — user wants immediate batch results
- New `draft_reviews` table: rejected — `proposal_reviews` already maps to sections

---

## Part 2: Render Draft Content

### A. Section Detail Page (`section_detail.html`)

**New tab:** "Draft Content" inserted between "Notes" and "Compliance"

**Backend change** (`app.py:proposals_section_detail_page`):
- Query `SELECT * FROM proposal_section_drafts WHERE section_id = ? ORDER BY created_at DESC LIMIT 1`
- Pass `draft` dict to template

**Template additions:**
- If draft exists: show scrollable `<div>` with rendered `draft_content` (preserve paragraphs)
- Show draft metadata: status badge, confidence, generation_model, reviewed_by, reviewed_at
- If WriteGuard results exist in `metadata` JSON: show overall score badge (green/yellow/red)
- Action buttons: "Approve" → PUT `/api/govcon/drafts/{id}/approve`, "Reject" → PUT `/api/govcon/drafts/{id}/reject`
- If no draft: show "No draft generated yet" with link to AI Drafts tab on opportunity page

### B. Opportunity Detail Page (`detail.html`)

**Sections table change:**
- Add column "Draft" between "Priority" and "Words"
- Server-side (`app.py:proposals_detail_page`): query latest draft per section via subquery or JOIN
- Show draft status badge (draft / reviewed / approved / rejected)
- Show "View Draft" link to section detail page (with optional `#draft` anchor)

**Why server-side for badge:** Avoids N+1 client-side fetches; simple LEFT JOIN or subquery.

### C. API additions

The existing `/api/govcon/drafts/<id>/approve` and `/reject` endpoints already handle status transitions. No new API needed for basic workflow. The section detail page can call these directly via JS (same pattern as detail.html AI Drafts tab).

---

## Files to Modify

| # | File | Action |
|---|------|--------|
| 1 | `tools/govcon/run_writeguard_on_drafts.py` | **New** — V&V pipeline tool |
| 2 | `tools/dashboard/app.py` | `proposals_section_detail_page()` — add draft query; `proposals_detail_page()` — add draft status per section |
| 3 | `tools/dashboard/templates/proposals/section_detail.html` | Add Draft Content tab + draft viewer + action buttons |
| 4 | `tools/dashboard/templates/proposals/detail.html` | Add Draft column to sections table |
| 5 | `tools/manifest.md` | Register new tool |
| 6 | `features/proposals_icdev_content.feature` (or new file) | Add Behave scenarios for draft visibility and WriteGuard findings |

---

## Success Criteria

1. `python tools/govcon/run_writeguard_on_drafts.py --json` runs without error and creates:
   - 9 `proposal_reviews` rows (red_team, completed)
   - ≥1 `proposal_review_findings` per draft with dimension-based severity
   - `proposal_section_drafts.status` updated to `reviewed`
   - `proposal_sections.status` advanced based on score thresholds
2. Section detail page shows Draft Content tab with full rendered text for all 9 sections.
3. Opportunity detail sections table shows draft status badges.
4. Behave scenarios pass.
5. Coherence checker passes; ruff clean.
