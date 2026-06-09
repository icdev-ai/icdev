# pgp-vfy-03-d3: observability_canvas RLS / JSON-SQL / table-collision verification

**Task:** Fix residual RLS, JSON functions, and schema collisions in observability_canvas
**Verdict:** PASS — no additional code changes required (fix already in place from pgp-res-02)
**Date:** 2026-06-09
**Branch:** irad/feature (worktree pgp-vfy-03-d2)

## Summary

observability_canvas was already hardened in `pgp-res-02` (commit `c2b1b0777`) +
the d2 carry-over edit to `tools/observability_canvas/db/init_db.py` on this worktree:

- Default backend flipped `sqlite` → `postgresql` (inherits `ICDEV_STORAGE_BACKEND`)
- `get_connection()` PG branch uses `get_canvas_connection("OC_PG_DATABASE")` —
  disables the global RLS predicate (canvas tables have no `tenant_id` column)
- `icdev/` package mirror is byte-identical to root
- 0 table collisions vs other canvases
- 0 nested-JSON SQL calls in runtime code (Python `json.loads()` on TEXT columns)
- 0 `tenant_id` columns (deliberate, comment in `db/init_db.py:44-47` documents)

This audit serves as evidence-of-completion per the same pattern used by
`pgp-vfy-05-d3` (migration_canvas) and `pgp-vfy-10-d3` (boundary_canvas).

## Why a fix isn't needed

`tools/observability_canvas/db/init_db.py` already routes the PG branch through
`get_canvas_connection("OC_PG_DATABASE")` (lines 50-56) — the same RLS-safe
pattern used by boundary_canvas, qdc_canvas, aiml_canvas, and migration_canvas.
The 15 `od_*` / `odc_*` tables have no `tenant_id` column, so the global
`get_connection()` would inject `WHERE tenant_id IN (...)` and raise
`UndefinedColumn` on PG. `get_canvas_connection()` disables RLS via
`security_context=None`, which is the canonical canvas rule per CLAUDE.md
("Canvas db/init_db.py must use get_canvas_connection()").

## Three-check audit

### Check 1: Cross-canvas table collisions (pgp-sch-03 logic)

| Concern | Result |
|---|---|
| observability_canvas CREATE TABLE count | **15** |
| Cross-canvas collisions involving observability tables | **0** |

Method: extracted all `CREATE TABLE` names from every `icdev/tools/<canvas>/db/init_db.py`
and `tools/<canvas>/db/init_db.py`, deduplicated by canvas name, looked for table
names shared by ≥2 distinct canvases. The 3 collisions reported across the
whole tree (`declarations`, `if`, `names`) are all in OTHER canvases
(ai_augmentation, aiify, foundry, integrity) — none involve observability tables.

observability_canvas tables (all `od_*` / `odc_*` / `observability_*` prefixed):

```
observability_designs
od_assessments, od_audit, od_collab_sessions, od_snippets,
od_templates, od_ttp_coverage, od_versions
odc_gap_scores, odc_mitre_techniques, odc_otel_events,
odc_runbooks, odc_sdc_verifications, odc_sops, odc_technique_coverage
```

Prefix is the namespace — no other canvas touches `od_*` / `odc_*`.

### Check 2: Nested-JSON SQL (pgp-tx-01..03 rules)

| Pattern | Hits in observability_canvas |
|---|---|
| `json_each(`, `jsonb_each(` | **0** |
| `json_extract(`, `jsonb_extract(` | **0** |
| `json_array_length`, `jsonb_array_length` | **0** |
| `->>$`, `->>"` in runtime SQL | **0** |

The 15 tables store JSON graph data in TEXT columns (`graph_json`,
`config_json`, `findings_json`, `attributes`, etc.). Runtime code reads
these columns as TEXT and parses with Python's `json.loads()` — the
**explicitly correct pattern** per CLAUDE.md:

> Runtime SQL is authored for PostgreSQL; `translate_sql` is a thin SQLite
> init-fallback ONLY, never load-bearing. **Compute in Python** — read the
> raw JSON column and parse with `json.loads()`.

### Check 3: RLS / tenant_id (pgp-res-02 + canvas rule)

| Column | Count | Expected | Note |
|---|---|---|---|
| `tenant_id` in CREATE TABLE statements | **0** | 0 | Canvas tables have no tenant_id; RLS bypassed via `get_canvas_connection()` |
| `tenant_id` references in file | 1 | 1 | The single match is in the docstring at line 46, explaining the design choice |
| `classification` in CREATE TABLE statements | 6 | ≥0 | CUI markings on 6 tables (observability_designs, od_audit, odc_sops, odc_runbooks, + 2 more) |

The deliberate design: observability_canvas uses `get_canvas_connection("OC_PG_DATABASE")`
which sets `security_context=None` and disables the global RLS predicate.
This is consistent with the per-canvas pattern: every canvas (boundary, qdc,
aiml, agentic_ai, aiify, migration) uses `get_canvas_connection(<CANVAS>_PG_DATABASE)`
and bypasses RLS. Multi-tenant isolation is enforced at the database-instance
level (separate `oc_pg_*` databases per tenant), not at the row level.

## Verification — runtime smoke test (HTTP through running dashboard)

Dashboard running on `http://localhost:5050` with `ICDEV_ODC_ENABLED=true`.

| Blueprint route | Status | Response size |
|---|---|---|
| `GET /observability/` | **200** | 200 B (page) |
| `GET /observability/templates` | **200** | 200 B (page) |
| `GET /observability/mitre` | **200** | 200 B (page) |
| `GET /observability/api/health` | **200** | 48 B (JSON: `{"module":"observability_canvas","status":"ok"}`) |
| `GET /observability/api/templates` | **200** | 5 templates returned (Datadog, Sentinel, NSM, SOC, DoD IL4) |
| `GET /observability/api/snippets` | **200** | 8 snippets returned |

**No `UndefinedColumn`, no `permission denied`, no RLS predicate failure.**

Direct DB connection (PG side, via `get_canvas_connection`):

```python
from tools.observability_canvas.db.init_db import get_connection
conn = get_connection()
conn.execute('SELECT COUNT(*) FROM od_templates').fetchone()  # (5,)
conn.execute('SELECT COUNT(*) FROM od_snippets').fetchone()   # (8,)
```

icdev/ package mirror verified — `from icdev.tools.observability_canvas.db.init_db import get_connection`
resolves and returns the same StorageConnection. `filecmp.cmp` confirms the
init_db.py file is byte-identical between `tools/observability_canvas/db/init_db.py`
and `icdev/tools/observability_canvas/db/init_db.py`.

## Acceptance

- [x] PG branch uses `get_canvas_connection("OC_PG_DATABASE")` (no RLS collision)
- [x] Default backend is `postgresql` (inherits `ICDEV_STORAGE_BACKEND`)
- [x] SQLite path remains as documented fallback
- [x] 0 cross-canvas table collisions
- [x] 0 nested-JSON SQL calls in runtime code
- [x] 0 `tenant_id` columns (deliberate, RLS bypassed)
- [x] icdev/ mirror is byte-identical
- [x] All blueprint routes return 200 with valid data
- [x] `tools/dashboard/app.py` registers the canvas via `ICDEV_ODC_ENABLED=true` toggle
- [x] No code changes required
