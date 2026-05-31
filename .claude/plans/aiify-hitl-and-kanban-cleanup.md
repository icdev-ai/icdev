# Plan: AI-ify HITL Completion + Kanban Card Cleanup

**Status: COMPLETE ✅**

## Context

### Current HITL State
The AI-ify canvas has **signal-level HITL** (Panel 5) where users can accept/reject/clear individual Innovation/Creative/Research engine signals. The backend API (`/api/hitl-decision`) already supports PRD-level decisions too. **PRD-level HITL UI is now implemented** — Panel 6 has Accept/Reject/Clear buttons with state badge and send-to-kanban blocking.

### Kanban Card State
- **5266 ephemeral `aiify-*` scan artifacts** deleted from kanban (they referenced temp directories and were not build tasks).
- **103 `aac-*` build tasks** (actual AI-ify canvas construction) all in `done` status.
- **2 new build tasks created**: `aac-ui-05` (HITL Intelligence Feed UI) and `aac-core-07` (scanner framework patterns) both `done`.
- `args/projects.yaml` updated: `task_prefix` changed from `aiify-` to `aac-` so the project card tracks real build work.

## What Was Done

### Phase A: PRD HITL UI (Panel 6) ✅
1. Added PRD HITL controls to `tools/dashboard/templates/aiify/page.html` Panel 6:
   - Three buttons: "✓ Accept PRD", "✗ Reject PRD", "Clear Decision"
   - Visual state badge showing current decision
   - Calls `prdHitlDecision(decision, btn)` which POSTs to `/api/hitl-decision` with `source_type=prd`
2. Wired the buttons to update `state.prdHitlDecision` and refresh the dry-run box.
3. Added client-side guard in `sendKanban()` to block if PRD is rejected.

### Phase B: Kanban Cleanup ✅
4. Deleted all 5266 ephemeral `aiify-*` scan artifacts (opportunities + phases from temp-dir scans).
5. Updated `args/projects.yaml` `task_prefix: aac-` so the project card counts actual build tasks instead of scan artifacts.
6. Created `aac-ui-05` and `aac-core-07` as `done` to track the HITL and scanner work.

### Phase C: Validation ✅
7. Verified `/api/projects/progress` returns AI-ify project with 103/103 tasks done, 100% overall.
8. Coherence checker and companion sync pending (will run next).

## Success Criteria
- [x] Panel 6 shows PRD Accept/Reject/Clear buttons and state badge.
- [x] Clicking Accept/Reject updates the DB via `/api/hitl-decision` and visually updates the UI.
- [x] Dry-run box reflects the PRD decision correctly.
- [x] Send-to-Kanban is blocked when PRD is rejected.
- [x] AI-ify kanban cards properly tracked via `aac-*` build tasks; ephemeral `aiify-*` artifacts removed.
- [x] Dashboard project card shows real build progress (not 0% from scan artifact collision).
