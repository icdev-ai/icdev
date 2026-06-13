# PMA Gap Epics — V&V Results

**Date:** 2026-06-13  
**Task:** pma-vv-01  
**Classification:** CUI // SP-CTI

---

## Summary

End-to-end validation of all three PMA gap epics. 151 total assertions across 5 test files — **all passing**. Coherence gate green. Health check passes.

---

## Epic 1 — Coordination (pma-coord)

**What was built:** Meeting log and action item management for program coordination meetings.

| Component | File | Status |
|-----------|------|--------|
| Data layer | `tools/pma/meeting_coordinator.py` | Built during V&V (pma-coord code was absent from branch) |
| API routes (6) | `tools/dashboard/api/cpmp.py` | Added during V&V |

**API Surface:**
- `POST /api/cpmp/contracts/<id>/meetings` — create meeting log
- `GET  /api/cpmp/contracts/<id>/meetings` — list meetings
- `POST /api/cpmp/meetings/<id>/extract-actions` — AI-extract action items from notes
- `GET  /api/cpmp/contracts/<id>/meetings/overdue` — open items past due date
- `GET  /api/cpmp/meetings/<id>/action-items` — list action items for meeting
- `PUT  /api/cpmp/action-items/<id>` — HITL approval gate + status update

**AI Extractor:** Keyword-based sentence parser identifies action sentences (follow up, schedule, review, submit, etc.) and assigns heuristic owners (PM / Coordinator / Contractor). Deterministic fallback; replaceable with LLM call when `ICDEV_LLM_PROVIDER` is configured.

**HITL Gate:** `hitl_approved` flag (INTEGER 0/1) on `pma_action_items`. Callers set via `PUT /action-items/<id>` with `hitl_approved: true` before dispatching.

**Overdue Alert:** `get_overdue_action_items()` finds open items with `due_date < today`, auto-transitions them to `status='overdue'`, and returns the list.

**V&V Steps Verified (5 assertions):**
1. Create meeting → 201, meeting_id returned
2. Extract action items → ≥2 items with PM/Contractor owners
3. HITL approve → `hitl_approved=1`, `status=in_progress`
4. Overdue alert → past-due item surfaced and auto-marked overdue
5. List meetings → created meeting present

---

## Epic 2 — Personnel Continuity (pma-cont)

**What was built:** Personnel registry with credential expiry monitoring and SPOF detection.

| Component | File | Status |
|-----------|------|--------|
| Data layer | `tools/pma/credential_monitor.py` | Pre-built |
| Genesis reflex | `tools/genesis/reflexes/pma_credential_monitor.py` | Pre-built |
| Personnel CRUD | `tools/govcon/personnel_manager.py` | Pre-built |
| API routes (7) | `tools/dashboard/api/cpmp.py` | Pre-built |

**Credential Severity Tiers:**
- `critical` — ≤ 30 days: alert inserted + kanban task seeded
- `warning` — ≤ 60 days: alert only
- `watch` — ≤ 90 days: alert only

**SPOF Detection:** Persons with `backup_person_id IS NULL` flagged as key-person single-points-of-failure; seeded as `cpmp_risks` with `category='staffing'`.

**Alert Dedup:** `UNIQUE(person_id, alert_type, expiry_date)` prevents duplicate alert rows across reflex firings.

**V&V Steps Verified (6 assertions):**
1. Insert person with poly expiring in 20 days → row in `pma_personnel`
2. `get_expiring_credentials()` surfaces person with `severity='critical'`
3. Full reflex run → `pma_credential_alerts` row inserted, `alerts_inserted ≥ 1`
4. Kanban task seeded with title containing person name / credential type
5. `get_key_person_dependencies()` flags person (no backup) as SPOF
6. Second reflex run → exactly 1 alert row (dedup confirmed)

---

## Epic 3 — INT Coverage Gap (pma-igap)

**What was built:** Intelligence collection gap tracking with automated requirement generation and compliance risk escalation.

| Component | File | Status |
|-----------|------|--------|
| Data layer | `tools/pma/int_gap_monitor.py` | Pre-built |
| Genesis reflex | `tools/genesis/reflexes/pma_int_gap_monitor.py` | Pre-built |
| API routes (8) | `tools/dashboard/api/cpmp.py` | Pre-built |

**Reflex Passes:**
1. Gap scan — `get_persistent_gaps(days=14)` surfaces unresolved gaps > 14 days old
2. Requirement generation — deterministic template-based with dedup key `(coverage_id, discipline)`
3. Compliance risk — critical gaps > 30 days with no `tasked` requirement → `cpmp_risks` entry

**HITL Tasking:** Collection requirements advance `open → tasked → satisfied` via `PUT /requirements/<id>`.

**V&V Steps Verified (9 assertions):**
1. Insert high-severity gap older than 14 days → row in `pma_int_gaps`
2. `get_persistent_gaps(days=14)` detects the gap
3. Full reflex run → `requirements_inserted ≥ 1`, `kanban_tasks_seeded ≥ 1` (high severity)
4. `pma_collection_requirements` row persisted with `discipline='HUMINT'`, `status='open'`
5. Manual status update `open → tasked` → confirmed in DB
6. Second reflex run → `requirements_inserted == 0` (dedup confirmed), count still 1
7. API coverage upsert → 201, `status='gap'`
8. AI-generate requirements via API → 2 requirements, `ai_generated=True`
9. HITL tasking via API → `status='tasked'`, `tasked_at` set

---

## Health Check

```
python tools/testing/health_check.py --json
success: true
checks: environment=pass, database=pass (1452 tables), python_deps=pass, tools=pass
```

---

## Coherence Gate

```
python tools/workflow/coherence_checker.py --all --gate
Result: 0 failing checks
```

**Fix applied during V&V:** Added `# rls-bypass: pma tables have no tenant_id/classification columns` annotations to all undocumented `set_security_context(None)` calls in `tools/govcon/personnel_manager.py`, `tools/pma/credential_monitor.py`, and `tools/pma/meeting_coordinator.py`.

---

## Test Results

| Test File | Tests | Result |
|-----------|-------|--------|
| `tests/test_pma_e2e_vv.py` | 20 | PASS |
| `tests/test_pma_credential_reflex.py` | 15 | PASS |
| `tests/test_pma_igap_reflex.py` | 39 | PASS |
| `tests/test_pma_personnel_api.py` | 57 | PASS |
| `tests/test_pma_igap_api.py` | 20 | PASS |
| **Total** | **151** | **ALL PASS** |

---

## Findings

**pma-coord gap closed:** Tasks pma-coord-01/02/03 were marked `done` in kanban but the implementation was not present on the branch. `tools/pma/meeting_coordinator.py` and 6 API routes were built during this V&V session to close the gap. pma-coord-04 (UI tab) remains `in_progress` as it was already tracked.

**pma-igap tables lazy-init:** `pma_int_gaps` and `pma_collection_requirements` are not in the live DB yet (only `pma_personnel` and `pma_credential_alerts` are). Both data-layer modules use `_ensure_tables()` / `conn.executescript()` for idempotent creation on first use.

**Acceptance Criteria:** Met — zero failing assertions across all three epics, coherence gate green.
