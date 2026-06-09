# pgp-vfy-03-d3: observability_canvas RLS / JSON / collision verification

**Task:** Fix residual RLS, JSON functions, and schema collisions in observability_canvas
**Branch:** irad/feature (worktree pgp-vfy-03-d2)
**Verdict:** PASS — audit-only, no code changes required
**Date:** 2026-06-09

## What this task verified

Three checks against observability_canvas to confirm the PGP remediation
stack (pgp-res-02, pgp-tx-01..03, pgp-sch-03) left this canvas clean:

1. **Table collisions** (pgp-sch-03 logic) — observability tables don't collide
   with any other canvas's tables. All 15 tables are `od_*` / `odc_*` /
   `observability_*` prefixed; the prefix is the namespace.
2. **Nested-JSON SQL** (pgp-tx-01..03) — zero `json_each` / `json_extract` /
   `json_array_length` calls in runtime code. The 15 tables store JSON
   graph data in TEXT columns; runtime code reads the TEXT and parses
   with Python `json.loads()` (CLAUDE.md canonical pattern).
3. **RLS / tenant_id** (pgp-res-02) — zero `tenant_id` columns by design.
   PG branch routes through `get_canvas_connection("OC_PG_DATABASE")` which
   disables the global RLS predicate (canvas tables have no `tenant_id`).

## Fix already in place

`tools/observability_canvas/db/init_db.py` was hardened in pgp-res-02
(commit `c2b1b0777`) and the d2 worktree carry-over edit:

- Default backend flipped `sqlite` → `postgresql` (inherits `ICDEV_STORAGE_BACKEND`)
- `get_connection()` PG branch uses `get_canvas_connection("OC_PG_DATABASE")`
  instead of the RLS-injecting `get_connection()`
- icdev/ mirror is byte-identical (verified via `filecmp.cmp`)

## Smoke test (HTTP through running dashboard)

All 6 blueprint routes return 200 with valid data:

| Route | Status | Notes |
|---|---|---|
| `GET /observability/` | 200 | index page |
| `GET /observability/templates` | 200 | template list page |
| `GET /observability/mitre` | 200 | MITRE matrix page |
| `GET /observability/api/health` | 200 | `{"module":"observability_canvas","status":"ok"}` |
| `GET /observability/api/templates` | 200 | 5 templates (Datadog, Sentinel, NSM, SOC, DoD IL4) |
| `GET /observability/api/snippets` | 200 | 8 snippets |

No `UndefinedColumn`, no `permission denied`, no RLS predicate failure on any path.

## Files touched

- `docs/audits/pgp-vfy-03-d3-observability-canvas-schema.md` (new) — audit report
- `docs/features/phase-pgp-vfy-03-d3-observability-canvas.md` (this file) — feature doc

## Related audits

- `pgp-vfy-05-d3` (migration_canvas) — same pattern, audit-only PASS
- `pgp-vfy-10-d3` (boundary_canvas) — same pattern, audit-only PASS
- `pgp-vfy-07-d3` (qdc_canvas) — same fix applied earlier
- `pgp-vfy-09-d2` (aiml_canvas) — same fix applied earlier

## Acceptance

- [x] Default backend is `postgresql`
- [x] PG branch uses `get_canvas_connection("OC_PG_DATABASE")`
- [x] 0 cross-canvas table collisions
- [x] 0 nested-JSON SQL calls
- [x] 0 `tenant_id` columns (deliberate, RLS bypassed)
- [x] icdev/ mirror byte-identical
- [x] All blueprint routes return 200
- [x] No code changes required (deliverable = audit report)
