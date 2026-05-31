# Plan: AI-ify HITL Intelligence Feed UI

## Problem Statement

The AI-ify canvas backend has **full HITL support** (`api_intelligence_feed`, `api_hitl_decision`, PRD filtering by HITL state, DB schema with `aiify_hitl_decisions`), but the **frontend wizard has zero UI** for it. Users cannot see Innovation, Creative, or Research engine signals, and cannot accept/reject them before PRD generation.

## Audit: What's Implemented vs Missing

| Feature | Backend | Frontend | Status |
|---------|---------|----------|--------|
| Scan engine (`engine.py`) | ✅ | ✅ Panel 1-3 | Done |
| Opportunity scoring + pros/cons | ✅ | ✅ Panel 4 | Done |
| Roadmap generator (`roadmap_generator.py`) | ✅ | ✅ | Done |
| PRD generation with Mermaid diagram | ✅ | ✅ Panel 5 | Done |
| Kanban promotion (`api_send_to_kanban`) | ✅ | ✅ Button on Panel 5 | Done |
| Intelligence feed API (`api_intelligence_feed`) | ✅ | ❌ Not called | **Missing** |
| HITL decision API (`api_hitl_decision`) | ✅ | ❌ No buttons | **Missing** |
| PRD filters rejected signals | ✅ | N/A (backend) | Done |
| HITL state persistence (`aiify_hitl_decisions` table) | ✅ | N/A | Done |

## Root Cause

The frontend template (`page.html`) has 5 wizard steps but **no step or panel** for reviewing Innovation/Creative/Research signals. The intelligence feed data is fetched during PRD generation on the backend, but the user never sees it or interacts with it.

## Solution

Insert a new wizard step **"5 Review & Approve"** between Results (4) and PRD (becomes 6). This step displays the three engine signal streams with HITL accept/reject/clear controls.

### Stepper Flow (After)

1. Source → 2. Options → 3. Scan → 4. Results → **5. Review & Approve** → 6. PRD & Diagram

### Panel 5 — "Review & Approve" Content

- **Header**: "Intelligence Review — Curate signals before they enter the PRD"
- **Three sections** (horizontal cards or accordion):
  1. **Innovation Signals** (`/api/intelligence-feed` → `innovation[]`)
  2. **Creative Pain Points** (`/api/intelligence-feed` → `creative[]`)
  3. **Research Regulatory Context** (`/api/intelligence-feed` → `research[]`)
- **Per-signal card**:
  - Title + truncated description
  - Score badge
  - HITL decision status (none / accepted ✓ / rejected ✗)
  - Action buttons: **Accept**, **Reject**, **Clear**
- **Call HITL API** (`POST /api/hitl-decision`) immediately on button click
- **Footer action**: "Generate PRD & Diagram →" (navigates to Panel 6)

### Panel 6 — "PRD & Diagram" (Renumbered)

Same as current Panel 5. No functional changes — the backend already filters rejected signals.

## Files to Modify

1. `tools/dashboard/templates/aiify/page.html` — Add Panel 5 HTML, CSS, JS; renumber Panel 5→6
2. `icdev/tools/dashboard/templates/aiify/page.html` — Mirror
3. `tools/aiify/blueprint.py` — Minor: ensure `api_intelligence_feed` CORS/jsonify OK (already is)

## Acceptance Criteria

- [ ] Stepper shows 6 steps with "Review & Approve" as step 5
- [ ] Panel 5 loads `/api/intelligence-feed` and renders 3 categories
- [ ] Each signal shows Accept/Reject/Clear buttons
- [ ] Clicking a button calls `/api/hitl-decision` and updates the card visual state
- [ ] Rejected signals are visually dimmed; accepted signals get a green border/check
- [ ] "Generate PRD →" button on Panel 5 navigates to Panel 6
- [ ] Panel 6 (PRD) renders normally; rejected signals do NOT appear in PRD markdown
- [ ] `pytest tests/test_aiify_scoring.py` still passes
- [ ] Both template copies (`tools/` + `icdev/`) are identical

## No-Go / Boundaries

- **No backend changes** to scoring, roadmap, or PRD logic — backend is already correct.
- **No new DB tables** — `aiify_hitl_decisions` already exists.
- **No changes** to Step 1-4 or Step 6 (PRD) content — only add the new Panel 5.
- Keep CSS scoped to `.aiify-wrap` — no global style leaks.
