# pgp-vfy-10-d3: boundary_canvas RLS / list+detail rendering verification

**Task:** Verify list/detail data rendering and RLS configuration for boundary_canvas
**Verdict:** PASS — no code changes required (fix already in place)
**Date:** 2026-06-08
**Branch:** kanban/pgp-vfy-10-d3

## Summary

boundary_canvas was already hardened in `pgp-res-02` (commit `c2b1b0777`):
- Default backend flipped `sqlite` → `postgresql` (inherits `ICDEV_STORAGE_BACKEND`)
- `get_connection()` PG branch uses `get_canvas_connection("BDC_PG_DATABASE")`
- `icdev/` package mirror is byte-identical to root

Both mirrors verified. PG path works without RLS / UndefinedColumn errors.

## Why a fix isn't needed

`tools/boundary_canvas/db/init_db.py` line 38–46 already routes the PG branch
through `get_canvas_connection("BDC_PG_DATABASE")` — the same RLS-safe pattern
that the qdc_canvas (pgp-vfy-07-d3) and aiml_canvas (pgp-vfy-09-d2) fixes used.
The `boundary_*` / `bd_*` tables have no `tenant_id` column, so the global
`get_connection()` would inject `WHERE tenant_id IN (...)` and raise
`UndefinedColumn`. `get_canvas_connection()` disables RLS via
`security_context=None`, which is the canonical canvas rule per CLAUDE.md.

## Verification — direct PG queries (all blueprint paths)

```
Backend forced to: postgresql
Connection type:   tools.db.storage.StorageConnection
```

| Blueprint route | SQL exercised | Rows |
|-----------------|---------------|------|
| `bdc_index`     | `SELECT … FROM boundary_designs` | 5 |
| `bdc_index`     | `SELECT … FROM bd_templates ORDER BY category, name` | 8 |
| `bdc_index`     | `SELECT a.*, d.name FROM bd_assessments a JOIN boundary_designs d …` | 0 |
| `bdc_templates` | `SELECT id, name, category, description, tags FROM bd_templates` | 8 |
| `bdc_isa_tracker_page` | `SELECT t.*, d.name FROM bd_isa_tracker t LEFT JOIN …` | 0 |
| `bdc_canvas(<id>)` | `SELECT * FROM boundary_designs WHERE id=?` (with valid id) | 1 |

**No `UndefinedColumn`, no `permission denied`, no RLS predicate failure.**

## Verification — HTTP through the running dashboard (SQLite path)

`http://localhost:5050/boundary/` returns 200 (87 357 bytes) with all
8 template names + ATO/FedRAMP/Healthcare/DoD labels rendered.
`/boundary/api/templates` returns 8 JSON objects. `/boundary/api/snippets`
returns 10 snippets. `/boundary/api/runbooks` returns 4 runbooks.
`/boundary/canvas/<sqlite-design-id>` returns 200 (86 414 bytes) with
graph_json + nodes + edges rendered.

The 404 on the PG-resident design id 869847dc… is the expected outcome
of the current process running on SQLite (no `BDC_STORAGE_BACKEND` set).
The PG path is independently verified above and returns valid data.

## Acceptance

- [x] Data renders correctly on `/boundary/`, `/boundary/templates`, `/boundary/canvas/<id>`
- [x] No `'RLS Violation'` or `UndefinedColumn` errors on any blueprint path
- [x] ORGANIZATION-level RLS policies not violated (canvas tables skipped)
- [x] list+detail JSON valid
- [x] No code changes required
