# TSR CANV — clean-worktree seed verification (tsr-canv-01-d2)

Evidence only. Produced 2026-08-01 on branch `kanban/tsr-canv-01-d2`, a worktree off `origin/main`
at `180348f91`. No source file was modified.

Answers: does the three-command seed sequence populate a clean worktree database well enough for the
CANV slice (`tsr-canv-01-d1`) to run — and is the mandated order actually load-bearing.

## Result

All three scripts exit `0`. The database ends at **541 tables**, including the six `canvas_*` tables
and all sixteen `studio_*` tables. Acceptance criterion met.

| # | command | exit | effect on a cold database |
|---|---------|------|---------------------------|
| 1 | `python tools/db/init_icdev_db.py` | 0 | creates **525** tables; **zero** `studio_*` |
| 2 | `python tools/studio/init_db.py` | 0 | creates the **16** `studio_*` tables → 541 |
| 3 | `python tools/db/migrations/311_studio_event_tables_rls_columns/up.py` | 0 | adds `classification`/`tenant_id` to 3 event tables |

Canvas tables present after the run:

```
canvas_access_grants  canvas_instances  canvas_kg_build_log
canvas_kg_edges       canvas_kg_nodes   canvas_projects
```

## The order is mandatory, not stylistic

Each step is load-bearing; none is a no-op. Verified by seeding throwaway databases via a
per-command `ICDEV_DB_PATH` (never exported — a leaked `ICDEV_DB_PATH` redirects every later tool):

- **Step 1 does not create the studio tables.** After `init_icdev_db.py` alone the database holds
  525 tables and `studio_*` is empty. `tools/studio/init_db.py` owns that DDL, which is why
  `migrate.py --up` is not a substitute — the studio schema is not a migration.
- **Step 3 fails without step 2.** Run directly after step 1 it raises and exits `1`:

  ```
  sqlite3.OperationalError: no such table: studio_event_sources
  ```

Re-running the full sequence on an already-seeded database is idempotent: step 2 reports
`16 created, 0 existing` cold and `0 created, 16 existing` warm; step 3 tolerates duplicate columns
by design (see the migration's docstring).

## Two things that will bite the next session

### 1. The backend must be pinned to SQLite

The kanban scheduler exports `ICDEV_STORAGE_BACKEND=postgresql` (plus `ICDEV_PG_NO_FALLBACK=true`)
into every dispatched session. Inherited unchanged, the three seeds target the **shared live
PostgreSQL instance**, not the worktree — `data/icdev.db` is never touched:

```
$ python -c "from tools.db.storage import is_pg; print(is_pg())"
True                                    # inherited env
$ ICDEV_STORAGE_BACKEND=sqlite python -c "from tools.db.storage import is_pg; print(is_pg())"
False                                   # pinned
```

`tests/conftest.py` forces `ICDEV_STORAGE_BACKEND=sqlite`, so a worktree database is only useful as
SQLite. The sequence as actually run:

```bash
ICDEV_STORAGE_BACKEND=sqlite ICDEV_PG_NO_FALLBACK=0 python tools/db/init_icdev_db.py
ICDEV_STORAGE_BACKEND=sqlite ICDEV_PG_NO_FALLBACK=0 python tools/studio/init_db.py
ICDEV_STORAGE_BACKEND=sqlite ICDEV_PG_NO_FALLBACK=0 python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

### 2. Running a script *by path* executes the shared checkout, not the worktree

Python puts the **script's** directory on `sys.path[0]` and does not add the cwd. `tools` therefore
misses the worktree copy and falls through to the editable install
(`__editable__.icdev-1.2.29.pth`), which is rooted at `C:\AI\ICDev` — the shared checkout:

```
$ python -c "import tools; print(tools.__file__)"      # cwd on sys.path
C:\AI\ICDev\.tmp\worktrees\tsr-canv-01-d2\tools\__init__.py
$ python .tmp/probe.py                                  # script dir on sys.path
C:\AI\ICDev\tools\__init__.py
```

So all three seeds ran **shared-checkout code** while writing to the **worktree's**
`data/icdev.db`. Benign in this run and only in this run: the shared checkout sat at `b17b53e39`
against the worktree's `180348f91`, but all four relevant sources were byte-identical
(`tools/db/init_icdev_db.py`, `tools/studio/init_db.py`, the 311 migration, and
`icdev/tools/db/storage.py`). If the shared checkout ever drifts on these files, a worktree seeded
this way gets the **other branch's** schema with no warning. Compare before trusting the seed, or
invoke via `python -m` from the worktree root.

## Smoke check

A canvas slice from the `tsr-canv-01-d1` Tier A inventory, run against the seeded database:

```
pytest tests/test_qdc_canvas.py tests/test_penta_aadc_initdb.py tests/test_network_check_constants.py
31 passed in 54.98s
```

## Out of scope — one gap left standing

`studio_run_memory` has neither `classification` nor `tenant_id`. Migration 311 covers only the
three event tables (`studio_event_sources`, `studio_workflow_triggers`, `studio_trigger_events`);
migration 309 covered `studio_workflow_runs` and `studio_workflow_run_steps` and did not include
`studio_run_memory`. Any read of that table inside a request context will hit the same silent
`no such column: classification` failure 311 was written to fix. Not fixed here — it needs its own
migration, which is outside this task.

## Why this document is the artifact

`data/*.db` is gitignored (`.gitignore:9`), so a seeded worktree database produces no committed
output. This file is the evidence for the run.
