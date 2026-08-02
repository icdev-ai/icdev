# TSR FLOW epic — test slice baseline (`tsr-flow-01-d1`)

Scoping run for the `flow` epic of the TSR card: packages `tools/workflow_hitl`, `tools/kanban`,
`tools/anvil`. Produced in a clean worktree on `kanban/tsr-flow-01-d1` off `origin/main`
(`1453f378b`), 2026-07-31.

Companion artifacts:

- `tsr-flow-01-slice.txt` — the 78 pytest-collectible files in scope (the runnable slice)
- `tsr-flow-01-triage.json` — per-file pass/fail/error counts + failure signatures

## Headline

**7 failing files, 27 failed + 9 errors, out of 78 files / 1,073 tests (1,037 passing).**
Slice runs in ~2m08s. `tools/anvil` is **fully green**.

## Environment — seed recipe and a mandatory `unset`

Seeded per the known-good recipe, with the backend pinned (unpinned, scripts 2 and 3 write to
PostgreSQL while script 1 writes SQLite, and the DB looks seeded but isn't):

```bash
cd <worktree>
export PYTHONPATH="<worktree-abs-path>"
export ICDEV_STORAGE_BACKEND=sqlite
python tools/db/init_icdev_db.py
python tools/studio/init_db.py --json
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

Note the second script is `tools/studio/init_db.py` — the task card said `init_db.py` without a
path, and there are 30+ files by that name. Verified after seeding: **541 tables, 16 `studio_*`,
`classification` + `tenant_id` present on `studio_event_sources`.**

**`ICDEV_PG_NO_FALLBACK` must be unset before running the slice.** It is exported in the ambient
session environment (it is *not* in the repo or the worktree `.env`), and
`tools/db/backend_guard.py` refuses to start the dashboard when it is true while
`ICDEV_STORAGE_BACKEND=sqlite`. Leaving it set inflates the baseline with 37 phantom errors:

| file | with ambient var | with it unset |
|---|---|---|
| `tests/test_dashboard_kanban.py` | 11 errors | **11 passed** |
| `tests/test_nav_sec_06_mutation_rbac.py` | 26 errors, 26 passed | 8 failed, 44 passed |

All numbers in this document are from the clean run (var unset). Anyone re-running this slice
under the kanban runner's environment will see the inflated figures and should not treat them as
regressions.

## Failing files by package

### `tools.workflow_hitl` — 1 file, 11 failed

| file | failed | errors | passed |
|---|---|---|---|
| `tests/test_workflow_hitl_engine.py` | 11 | 0 | 12 |

**This is test pollution, not a defect in the file.** Run alone it is fully green
(21 passed, 2 skipped). The 11 failures reproduce from just the three hitl files together:

```bash
pytest tests/test_workflow_hitl_api.py tests/test_workflow_hitl_engine.py tests/test_workflow_hitl_report.py
# -> 11 failed, 47 passed
```

So the polluter is `test_workflow_hitl_api.py` or `test_workflow_hitl_report.py`, both within the
epic. The failure signature is also **not stable across runs** — it surfaced as
`no such table: wf_templates` in one batch and `FOREIGN KEY constraint failed` in the next,
which is consistent with shared-DB teardown order rather than a fixed schema gap. Isolating the
polluter is the first FLOW work item; it is worth 11 of the epic's 36 failures on its own.

> **RESOLVED** (`0393c7808`, `tsr-flow-01-d2`, 2026-08-01; re-verified under `tsr-agent-01-d4`).
> The polluter was `test_workflow_hitl_api.py`, and the cause was not teardown order: the `tmp_db`
> fixture patched only `tools.db.storage.get_connection`, so modules that had already run
> `from tools.db.storage import get_connection` kept the original function object and read the
> ambient DB. The unstable signature follows from that — the failure mode is "wrong database", so
> the error depends on the ambient DB's state. Now **23 passed** solo and order-independent.
> Full analysis: [`tsr-agent-01-d4-hitl-pollution.md`](tsr-agent-01-d4-hitl-pollution.md).

### `tools.kanban` — 5 files, 14 failed + 9 errors

| file | failed | errors | passed | root cause |
|---|---|---|---|---|
| `tests/test_nav_sec_06_mutation_rbac.py` | 8 | 0 | 44 | `POST /api/ai-wizard/submit -> 403`, `POST /api/aisg/patterns/seed -> 403` — AISG/ai-builder RBAC, **not kanban** |
| `tests/ci/test_pr_watcher.py` | 3 | 0 | 16 | `_FakeConnection` test double has no `.cursor`; gate falls into "verification unreadable, holding" instead of `refusing auto-merge` |
| `tests/test_loop_engineering.py` | 2 | 0 | 21 | `assert 'a2' in []` (L384), `assert 0 == 1` (L401) |
| `tests/test_dsyn_hitl_task.py` | 1 | 0 | 7 | `AttributeError` monkeypatching `tools.genesis.reflexes.dic_review_cadence` |
| `tests/test_kanban_pause_expiry_visibility.py` | 0 | 9 | 0 | all 9 fail **on setup**: `tools.kanban.scheduler_control` has no attribute `_FLAG` |

Two of these are ownership-ambiguous and should probably be reassigned rather than fixed here:

- `test_nav_sec_06_mutation_rbac.py` reaches kanban only via `mock.patch` target strings
  (`tools.kanban.build_mode`, `.model_override`, `.scheduler_control`); its 8 real failures are
  all AISG/ai-builder RBAC. This is the over-counting the TSR card warns about.
- `test_dsyn_hitl_task.py` touches `tools.kanban.task_factory` but fails in a genesis reflex.

`test_kanban_pause_expiry_visibility.py` is the cleanest genuine kanban defect: the test
monkeypatches a private `_FLAG` that `scheduler_control` no longer exposes — a renamed private,
so either the test or the module drifted. Worth checking against
`tools/kanban/scheduler_control.py` history before changing the test, since pause-control
behaviour is the subject of several known board bugs.

### `tools.anvil` — 0 failing files

Green across the slice. `tests/security/test_anvil_runner.py` and the anvil-named files all pass.

### Filename-keyed only (no `tools.X` import) — 1 file, 2 failed

| file | failed | passed | signature |
|---|---|---|---|
| `tests/genesis/test_kanban_message_injection.py` | 2 | 4 | `expected 2 invocations, got 0` (L112), `assert 0 == 1` (L165) |

## Scope: how the 78 files were selected

Three discovery passes, because import-only scanning under-scopes this epic badly:

| method | files |
|---|---|
| strict `from/import tools.<pkg>` | 43 |
| any `tools.<pkg>` reference (incl. `mock.patch` strings) | 55 |
| filename contains `hitl`/`kanban`/`anvil` | 50 |
| **union** | **85** |
| union minus non-collectible | **78** |

The filename-keyed pass matters: **30 files match by name with no `tools.X` reference at all**,
including 10 `test_kanban_*.py` files (`test_kanban_auto_revive`, `test_kanban_depends_on`,
`test_kanban_startup_recovery`, …) and the whole `tests/genesis/test_kanban_*` group. An
import-only scan would have dropped every one of them. This is the same failure mode recorded for
the DASH epic, where `tools/nav` does not exist as a package.

Conversely the reference pass over-counts: files matching only inside `mock.patch("tools.kanban…")`
strings mostly belong to other epics (see `test_nav_sec_06` above). Both directions are recorded
in `tsr-flow-01-triage.json` via the `packages` field so the next session can re-slice either way.

### 7 files in scope that pytest does not collect

`pyproject.toml` sets the default `python_files = test_*.py`, so these are **never run by the
suite** despite being on disk:

```
tests/e2e_chat_hitl_kanban_lifecycle.py
tests/e2e_kanban_bulk_promote.py
tests/e2e_kanban_depends_on.py
tests/e2e_kanban_depends_on_full_lifecycle.py
tests/innovation/e2e_kanban_promoter.py
tests/security/e2e_anvil.py
tests/tools/anvil/agentic_runner_reasoned_test.py
```

The first six are `e2e_*` and presumably driven by `e2e_runner.py` rather than pytest. But
`tests/tools/anvil/agentic_runner_reasoned_test.py` uses a `*_test.py` suffix that matches no
runner in this repo — it is dead weight unless something invokes it explicitly. Flagging rather
than fixing; renaming it would add an unreviewed file to the anvil baseline.

## Suggested FLOW work order

1. Isolate the `test_workflow_hitl_api/report` polluter — 11 failures, one root cause.
2. `test_kanban_pause_expiry_visibility.py` `_FLAG` drift — 9 errors, genuine kanban.
3. `test_pr_watcher.py` `_FakeConnection.cursor` — 3 failures, a test double missing a method.
4. Reassign `test_nav_sec_06_mutation_rbac.py` (8, AISG RBAC) and `test_dsyn_hitl_task.py`
   (1, genesis reflex) to their real epics.
5. `test_loop_engineering.py` (2) and `test_kanban_message_injection.py` (2).

Per the TSR card: several of these need a judgment call about whether the test or the code is
wrong. In particular #2 and #3 could each be "fixed" by weakening an assertion about
merge-gating or pause behaviour — both are areas with known board bugs, so the code side should
be checked first.
