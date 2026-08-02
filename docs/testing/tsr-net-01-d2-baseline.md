# TSR NET — clean-worktree vs shared-checkout failure baseline (tsr-net-01-d2)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-net-01-d2-r2`, a worktree off
`origin/main` at `0f9544b0a`. No source or test file was modified.

Follows [`tsr-net-01-import-inventory.md`](tsr-net-01-import-inventory.md) (tsr-net-01-d1), which
identified the slice. Answers the question the NET epic is blocked on: **of the failures in the
network/infrastructure slice, which are artefacts of a stale or under-seeded checkout and which are
genuine defects?**

## Headline

**None of them are ambient state.** All 18 failing files fail *identically* — same file, same test
count, same pass/fail split — in a freshly seeded clean worktree and in the long-lived shared
checkout at `C:\AI\ICDev`, whose `data/icdev.db` carries 920 tables against the clean worktree's
545. There is no seeded-data category to subtract.

| category | files | meaning |
|----------|-------|---------|
| **real defect** | **18** | fails in both environments — fix the code or the test |
| setup issue | 0 | would mean: fails only for want of seeded DB state |
| shared-only failure | 0 | would mean: fails only in the shared checkout |
| clean both | 113 | passes in both |

131 files measured, 2205 tests collected in each environment, **2091 passing and 111 failing —
the same numbers on both sides, to the test.**

**Consequence for the rest of the NET epic: work it in a clean worktree.** The shared checkout's
375 extra tables confer no advantage on this slice, and every remediation task can be verified in
isolation.

## Before / after per environment

| metric | clean worktree | shared checkout |
|--------|---------------|-----------------|
| root | `C:/AI/.worktrees/tsr-net-d2-r2` | `C:/AI/ICDev` |
| HEAD | `0f9544b0a` | `f9e9b00a6` |
| working tree | clean, freshly seeded | dirty |
| `data/icdev.db` tables | 541 after seed (545 after the run) | 920 |
| files measured | 131 | 131 |
| files clean | 113 | 113 |
| files failing | 18 | 18 |
| files collecting 0 tests | 1 | 1 |
| tests collected | 2205 | 2205 |
| tests passed | 2091 | 2091 |
| tests failed/errored | 111 | 111 |
| tests skipped | 3 | 3 |

Per-file numbers and error signatures: [`tsr-net-01-d2-triage.json`](tsr-net-01-d2-triage.json).

## Method

### Environment A — clean worktree

`C:\AI\ICDev\.tmp\worktrees\tsr-net-01-d2`, the directory this card was dispatched into, **is not a
git worktree.** It has no `.git`, so git resolves it to `C:/AI/ICDev` and reports branch `main`; a
commit made there would land on the shared checkout's `main`. A fresh worktree was created instead:

```bash
git worktree add -b kanban/tsr-net-01-d2-r2 C:/AI/.worktrees/tsr-net-d2-r2 origin/main
```

Seeded with the three steps the TSR card mandates, both env pins load-bearing:

```bash
export PYTHONPATH='C:\AI\.worktrees\tsr-net-d2-r2'   # else: ModuleNotFoundError: No module named 'tools'
export ICDEV_STORAGE_BACKEND=sqlite                  # else the seed half-lands against an unreachable PG
python tools/db/init_icdev_db.py                                       # 525 tables
python tools/studio/init_db.py                                         # studio tables
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py    # Migration 311 applied
```

541 tables, 8.9 MB.

### Environment B — shared checkout

`C:\AI\ICDev` as it stands: dirty working tree, `data/icdev.db` at 920 tables from months of dev
use. No seeding, no cleaning — the point is to measure it as-found.

### Execution

Both environments ran the **same 131 files** from
[`tsr-net-01-slice.txt`](tsr-net-01-slice.txt), **one pytest process per file**:

```
python -m pytest <file> -p no:cacheprovider --tb=no -q \
    --continue-on-collection-errors --timeout=90 --timeout-method=thread \
    --junit-xml=<file>.xml
```

Per-file rather than chunked: collection errors are the expected failure mode here, and a single
combined run loses attribution the moment one file hangs or hard-crashes the process. Counts and the
first failure signature are read back from the JUnit XML, not scraped from stdout.

`ICDEV_STORAGE_BACKEND=sqlite` is set for both (redundant — `tests/conftest.py` hard-sets it unless
`ICDEV_PYTEST_PG` is present), `ICDEV_DB_PATH` is unset, and `ICDEV_DASHBOARD_URL` is pinned to
`http://127.0.0.1:5050` so it cannot inherit the session's `host.docker.internal` value.

### Confounder check — the comparison is clean

Both trees were hashed before the runs.

| compared | files | differing |
|----------|-------|-----------|
| the 131 slice test files | 131 | **0** (after newline normalisation) |
| `.py` under `tools/{network,pipeline,ci,infra,cloud,ops_hub}` | 242 | **0** |

A raw byte comparison flags 8 files under `tests/genesis_auto/`; every one is CRLF-vs-LF only and
identical after `\r\n` → `\n`. **No measured file differs by code version between the two
environments**, despite the shared checkout being 1 commit off `origin/main` — that commit touches
nothing in scope.

## The 18 real defects, by failure family

| family | files | first-failure signature |
|--------|-------|-------------------------|
| **A — missing table** | 4 | `sqlite3.OperationalError: no such table: ni_devices` / `pdc_snapshots` |
| **B — `%s` on a raw sqlite3 connection** | 2 | `sqlite3.OperationalError: near "%": syntax error` |
| **C — patch target absent from module** | 3 | `AttributeError: module ... has no attribute 'get_connection'` / `'_fwd_reach_cache'` |
| **D — jinja2 undefined** | 1 | `jinja2.exceptions.UndefinedError: 'var' is undefined` |
| **E — assertion / contract drift** | 8 | varied; see JSON |

### A — missing table (4 files)

`tests/e2e_dual_track_lifecycle.py`, `tests/e2e_full_lifecycle_complex.py`,
`tests/e2e_ndc_full_lifecycle.py` all die at **collection** on `no such table: ni_devices`;
`tests/test_devops_twin_route.py` on `no such table: pdc_snapshots`.

**These are not seed gaps.** Both tables are absent from the shared checkout's 920-table database as
well as the clean worktree's 545 — no migration and no `init_icdev_db.py` path creates either. The
tables come from canvas-local `db/init_db.py` modules the tests never invoke. Fixing this is a
fixture change (or a migration), not "seed the DB harder".

`test_devops_twin_route.py` is the one file where the two environments disagree on *why* it fails,
and it is instructive: the clean worktree stops at `no such table: pdc_snapshots`, while the shared
checkout gets further and hits `jinja2.exceptions.UndefinedError: 'nav_tree' is undefined`. Same 1
pass / 4 fail on both sides, but **fixing the table will expose a second, independent template
defect underneath.** Budget for two fixes here, not one.

### B — `%s` on a raw sqlite3 connection (2 files)

`tests/test_network_ingester_e2e.py`, `tests/test_traffic_flow_walkthrough.py`. The repo authors
`%s` for PostgreSQL and only `StorageConnection` rewrites it to `?`; a fixture that patches
`get_connection` with a bare `sqlite3.connect` turns every statement into a syntax error. This is
the dominant TSR family and `tests/_sql_compat.py` already exists for it — these two are
mechanical.

### C — patch target absent from module (3 files)

`tests/test_dsyn_emit_ndc.py` and `tests/test_dsyn_emit_network.py` monkeypatch
`get_connection` on `tools.ndc.event_emitter` / `tools.network.event_emitter`; neither module
defines that name. `tests/test_advisory.py` patches `tools.network.advisory._fwd_reach_cache`,
likewise absent. Read the module before the test: either the symbol was renamed and the test was
never updated, or the test asserts a seam the code never had.

### D — jinja2 undefined (1 file)

`tests/test_terraform_generator.py` — 21 pass, 19 fail on `'var' is undefined`. A template renders
with `StrictUndefined` against a context that never supplies `var`.

### E — assertion / contract drift (8 files)

`tests/ci/test_pr_watcher.py`, `tests/ci/test_pr_watcher_sibling.py`,
`tests/e2e_network_nl_query.py`, `tests/test_federal_peering_request.py`,
`tests/test_ndc_blueprint_route_parity.py`, `tests/test_pdc_routes_engine.py`,
`tests/test_pdc_routes_misc.py`, `tests/test_remediation_simulator.py`.

Test and code disagree on a contract. Per the TSR card's brief, work out which is right rather than
deleting the assertion — `test_ndc_blueprint_route_parity.py` for instance names a concrete new
route (`/network/api/export/pptx/<topo_id>`) introduced by the blueprint split, which reads like a
real parity gap rather than a stale expectation.

**One caveat in this family.** `tests/test_pdc_routes_misc.py` asserts `... == 1` and gets `0` in
the clean worktree but `35` in the shared checkout. It fails in both, so the classification stands,
but its *symptom* is ambient-data-dependent: whoever fixes it must decide whether the test should
be seeding its own row (0 is what an empty DB gives) or filtering (35 is what months of dev data
gives). Do not fix it against the shared checkout alone.

## Not a defect, but worth recording

`tests/e2e_devops_twin.py` collects **0 tests** in both environments. It is an `e2e_*.py` script
without pytest-discoverable test functions, so it neither passes nor fails — it silently
contributes nothing. It is counted under "clean both" above; it is not evidence of health.

## Reproducing

```bash
cd C:/AI/.worktrees/tsr-net-d2-r2
export PYTHONPATH='C:\AI\.worktrees\tsr-net-d2-r2'
export ICDEV_STORAGE_BACKEND=sqlite
pytest $(cat docs/testing/tsr-net-01-slice.txt | tr '\n' ' ') -rfE --timeout=90
```

Use `-rfE`, not `-rf`: `-rf` hides ERRORs, and 3 of the 18 failing files fail at collection.

## Recommended dispatch order for the rest of the NET epic

1. **B (2 files)** — mechanical, `tests/_sql_compat.py` already exists.
2. **C (3 files)** — read the module, then decide rename-vs-missing-seam.
3. **A (4 files)** — one fixture/migration change unblocks 3 collection failures at once; budget a
   second fix for the `nav_tree` defect it will uncover in `test_devops_twin_route.py`.
4. **D (1 file)**, then **E (8 files)** — E is per-file judgement work, no shared root cause.

Work in a clean worktree throughout. This baseline shows the shared checkout tells you nothing extra
about this slice.

## Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-net-01-d2-baseline.md` | this document |
| `docs/testing/tsr-net-01-d2-triage.json` | per-file before/after counts, category, failure family, both error signatures |
