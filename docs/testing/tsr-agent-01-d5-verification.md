# tsr-agent-01-d5 — Final verification, ruff, and before/after counts (AGENT epic)

Closing card for the AGENT epic of the TSR test-suite remediation. Verifies the
combined effect of `tsr-agent-01-d3` (PR #1166) and `tsr-agent-01-d4` (PR #1169),
both merged to `main`.

## Method

Two worktrees created from the same `origin/main` commit (`80a8a46f1`), seeded
identically:

* **after** — `origin/main` as merged.
* **before** — `origin/main` with only the seven files touched by d3/d4 reverted.

`git stash` was **not** used to build the baseline, even though the card suggests
it: the stash stack is shared across every worktree of a repo, and a dozen other
sessions are live in this checkout. A reverse-patch into a dedicated worktree is
the equivalent that is safe under concurrency.

The baseline was built by **reverse-applying the d3 and d4 diffs**, not by
checking out the pre-fix files. That distinction is load-bearing: both copies of
`cross_agency_transfer_logger.py` were modified again *after* d3 by
`69a98df8b` (swp-scan-01, `recorded_at` → `created_at`). Checking out the pre-d3
file would have silently reverted that unrelated fix too and attributed its
effect to this epic. The one reverse-patch conflict was resolved to undo d3 only,
leaving `69a98df8b` intact.

Both DBs seeded with the backend pinned, and verified — **541 tables / 16
`studio_*`** in each:

```bash
PYTHONPATH=<wt> ICDEV_STORAGE_BACKEND=sqlite python tools/db/init_icdev_db.py
PYTHONPATH=<wt> ICDEV_STORAGE_BACKEND=sqlite python tools/studio/init_db.py --json
PYTHONPATH=<wt> ICDEV_STORAGE_BACKEND=sqlite python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

One pytest process **per file** — a module-scope `sys.exit()` anywhere in the
slice would otherwise abort the whole session and read as a mass failure.

## Results — before / after, per file

Slice: 5 test files, 125 tests (84 in the d3 half, 41 in the d4 half).

### d3 half — `%s` placeholders reaching raw `sqlite3` connections

| File | Before | After (as merged) | After + d5 fix |
|---|---|---|---|
| `tests/services/ingestion/test_hook_transfer.py` | 6 failed, 18 passed | 1 failed, 23 passed | **24 passed** (2.5s) |
| `tests/test_cross_agency_transfer_api.py` | 6 failed, 1 passed | 1 failed, 6 passed | **7 passed** (4.1s) |
| `tests/unit/test_audit_trail.py` | 9 failed, 10 passed, 4 errors | **19 passed** | **19 passed** (10.0s) |
| `tests/compliance/audit_nist.py` | 29 failed, 5 passed | **34 passed** | **34 passed** (6.8s) |

**d3 half: 34 passing before, 82 after as merged, 84 after the d5 fix.**
50 failed and 4 errored before; 2 failed after as merged; 0 failed after d5.

The before-column failures reproduce the cause the d3 doc claims —
`sqlite3.OperationalError: near "%": syntax error` where production code authors
PostgreSQL `%s` placeholders and a bare `sqlite3.connect` stands in for
`get_connection`.

### d4 half — `tmp_db` fixture namespace sweep

| Arrangement | Before | After |
|---|---|---|
| `tests/test_workflow_hitl_engine.py` alone | 23 passed (25.5s) | 23 passed (30.6s) |
| `test_workflow_hitl_api.py` then engine (the real polluter) | 41 passed (31.4s) | 41 passed (28.8s) |

**Identical, and that is the expected result.** d4's own doc states its change is
*latent hardening, not a fix for a live failure* — the load-bearing sweep landed
upstream of d4, and d4 extended it to the canonical `icdev.tools.*` namespace.
Every `workflow_hitl` import in the engine test file goes through the `tools/`
shim, so the canonical copies are loaded but never exercised.

Re-running the count that shows the change is not a no-op reproduces the d4 doc
exactly:

```
pre-d4 filter sweeps :   14
post-d4 filter sweeps:   28
modules d4 newly covers: 14
distinct module objects: True
```

`tools/` is a shim over `icdev.tools`, but `_ToolsRedirect` keeps its own
`__path__`, so both namespaces load the file into two distinct module objects
with independent `get_connection` bindings. The pre-d4 filter swept 14 of 28.
CLAUDE.md requires new code to import `icdev.tools.*`, so the first module that
follows that rule would reopen the leak in its original shape — symptom "wrong
database", not an import error.

## Remaining failures

**None.** No test in the slice fails.

Two failures were present on `main` as merged, and both are fixed by this card.
They are named here in full because the card asks for cause, and the cause is not
external:

* `tests/services/ingestion/test_hook_transfer.py::TestNistAu2Au9RealDb::test_run_transfer_appends_initiated_and_completed`
* `tests/test_cross_agency_transfer_api.py::TestSubmitTransfer::test_dual_write_to_audit_trail`

Both asserted `0` rows where they expected the AU-2 dual-write. Cause, measured
rather than inferred:

```
SWALLOWED EXCEPTION -> OperationalError: table audit_trail has no column named created_at
```

`69a98df8b` (swp-scan-01) correctly changed the mirror INSERT in
`cross_agency_transfer_logger.py` from `recorded_at` to `created_at` —
`recorded_at` exists only in the SQLite init DDL and never on PostgreSQL. It did
not update the two test fixtures that build their own local `audit_trail` table,
which still declared `recorded_at` and no `created_at`. The mirror INSERT then
raised into the logger's `except Exception`, was logged and swallowed, and the
dual-write assertion found an empty table.

This is the exact failure shape CLAUDE.md names in the swallowed-INSERT
guardrail: *"the INSERT then raises at runtime, is swallowed by the surrounding
`except Exception: pass`, and the feature reports success while persisting
nothing."* Here it was a test fixture rather than the live schema, so the fix is
two DDL lines, not a migration. `tests/compliance/audit_nist.py` already carried
`created_at` and is why it was the only d3 file at 34/34 on merge.

## A coverage finding — `tests/compliance/audit_nist.py` never runs

The file does not match pytest's default `python_files` (`test_*.py`,
`*_test.py`), and `pyproject.toml` does not override it. Directory collection
picks up **0** items from it:

```
pytest tests/compliance/ --collect-only -q   ->  0 audit_nist items
```

It runs only when named explicitly on the command line, as done here. None of the
five AGENT-slice files appear in the 47-file CI pytest allowlist either
(`.github/workflows/icdev-ci.yml:96`).

So the 34 tests d3 repaired in that file — the NIST AU-2 / AU-9 append-only and
SQL-injection coverage for cross-agency transfers — are executed by no automated
sweep at all. The fix is real and is verified above, but nothing on `main` will
notice if it regresses. Renaming the file to `test_audit_nist.py` would fix that
in one move. **Not done here** — it is a rename that changes what the default
suite collects, so it belongs in its own card with its own run, not in an epic
closing PR. Recommended as follow-up.

## ruff

`ruff check` — **clean**, using the exact command CI enforces
(`.github/workflows/icdev-ci.yml:36`), across all seven touched files including
this card's edits:

```
python -m ruff check <7 files> --select E,F,W --ignore E402,E501,E701,E702,E721,E722,E731,E741,F404
All checks passed!
```

The repo default config (`ruff.toml`, `line-length = 120`) also passes. **The
slice is ruff-clean.**

`ruff format` was run in `--diff` / `--check` mode and **deliberately not
applied.** It reports 5 of the 7 files would be reformatted (172 changed lines),
but this repo does not use the formatter:

* No workflow in `.github/workflows/` invokes `ruff format` — CI runs
  `ruff check` only.
* `ruff format --check tests/` reports **1692 of 2063 files would be
  reformatted** — 82% of the test suite.
* Reformatting `tools/audit/cross_agency_transfer_logger.py` would also have to
  be mirrored byte-for-byte into `icdev/tools/audit/`, adding mirror-parity risk
  for no gate benefit.

Applying it would add churn without buying gate compliance, and would bury this
card's two-line substantive fix in 172 lines of whitespace.

## Files touched by the epic

| File | Task |
|---|---|
| `tools/audit/cross_agency_transfer_logger.py` | d3 |
| `icdev/tools/audit/cross_agency_transfer_logger.py` | d3 (mirror) |
| `tests/compliance/audit_nist.py` | d3 |
| `tests/services/ingestion/test_hook_transfer.py` | d3, **d5** |
| `tests/test_cross_agency_transfer_api.py` | d3, **d5** |
| `tests/unit/test_audit_trail.py` | d3 |
| `tests/test_workflow_hitl_engine.py` | d4 |

## Re-verification against the current `main` (2026-08-02)

The measurements above were taken against `origin/main` at `80a8a46f1`. The whole
slice was re-run after rebasing this branch onto `1639fecd8`, against the same
seeded DB shape (**541 tables / 16 `studio_*`**, confirmed before the run), one
pytest process per file. Every count reproduced:

| File / arrangement | Re-run result |
|---|---|
| `tests/services/ingestion/test_hook_transfer.py` | 24 passed (17.9s) |
| `tests/test_cross_agency_transfer_api.py` | 7 passed (17.7s) |
| `tests/unit/test_audit_trail.py` | 19 passed (7.2s) |
| `tests/compliance/audit_nist.py` | 34 passed (17.7s) |
| `tests/test_workflow_hitl_engine.py` alone | 23 passed (20.7s) |
| `test_workflow_hitl_api.py` then engine | 41 passed (17.3s) |

**125/125 passing, 0 failed, 0 errored.** `ruff check` re-run over all seven
touched files: `All checks passed!` under both the CI command and the repo
default config.

One run-condition caveat worth recording, because it reads as a failure and is
not one: on the *first* invocation in a cold worktree, `test_cross_agency_transfer_api.py`
and `test_audit_trail.py` both tripped the configured `pytest-timeout` **during
module import**, with the traceback ending in `importlib._bootstrap_external.get_code`
— i.e. the timer expired while Python was compiling `.pyc` files, before a single
test ran. Both passed on the immediately following invocation with no code change
(`-o timeout=900`). The timeout is charged against import, not against the tests.
CI does not hit this: its `Test` job logs `PytestConfigWarning: Unknown config
option: timeout`, so the plugin is absent there and the setting is inert.

## External blocker (not a slice failure)

`main` is currently red for a reason unrelated to this epic, and it blocks this
PR from merging along with every other open PR:

```
FAILED tests/test_migration_version_uniqueness.py::test_no_new_duplicate_migration_versions
AssertionError: New duplicate migration version(s) detected:
  {'333': ['333_runtime_invocations', '333_sharepoint']}
```

Both directories are tracked at `origin/main` (`1639fecd8`), so the collision is
on the trunk, not introduced here — this branch touches no migration. **PR #1206
(`fix/migration-333-collision`) is already in flight to renumber it.** This card's
`Test` check will stay red until that lands, and no change on this branch can
clear it.

## Acceptance criterion

| Requirement | Status |
|---|---|
| PR body contains before/after pass-fail counts per file | ✅ table above, per file, both halves |
| All touched files are ruff-clean | ✅ `All checks passed!` under the CI command and the repo default, re-run on the current base |
| Remaining failures listed with cause | ✅ **none remain in the slice**; the two that existed on merge are named, root-caused to a measured `OperationalError`, and fixed. The one red check is `test_no_new_duplicate_migration_versions`, external to the slice, owned by PR #1206 |

---

## Third independent pass — current `main` base `83584c22c` (2026-08-02)

`main` advanced again after the second pass (`1639fecd8` → `83584c22c`, picking up
#1204, #1205 and #1208). The whole slice was re-run a third time from a **cold,
freshly-created worktree** on that base, with the two files this PR touches
checked out over it, one pytest process per file.

The DB in this worktree was seeded by `tools/db/init_icdev_db.py` alone
(**525 tables**) — deliberately *not* topped up with the pending migrations that
brought the earlier passes to 541. Every count still reproduced, which shows the
slice's results do not depend on the extra 16 tables:

| File / arrangement | Third pass |
|---|---|
| `tests/services/ingestion/test_hook_transfer.py` | 24 passed (5.0s) |
| `tests/test_cross_agency_transfer_api.py` | 7 passed (3.0s) |
| `tests/unit/test_audit_trail.py` | 19 passed (3.4s) |
| `tests/compliance/audit_nist.py` | 34 passed (10.8s) |
| `tests/test_workflow_hitl_engine.py` alone | 23 passed (21.7s) |
| `test_workflow_hitl_api.py` then engine | 41 passed (14.9s) |

**125/125 passing, 0 failed, 0 errored** — identical to both earlier passes.

`ruff check tests/services/ingestion/test_hook_transfer.py
tests/test_cross_agency_transfer_api.py` → `All checks passed!`

Two corrections to the sections above, now that the base has moved:

- The line "This card's `Test` check will stay red until that lands" is **no longer
  true**. The 333 renumber has landed on `main`, and all nine checks on this PR are
  green: Lint, Test, Test (PostgreSQL), Security Scan, Helm Lint, Doc Coherence
  Gate, E2E (Playwright) and Two-Tier LLM Build all report SUCCESS (Docker Build is
  skipped for a docs/tests change).
- The cold-worktree `pytest-timeout`-during-import caveat did **not** reproduce on
  this pass. It is a first-invocation `.pyc` compilation artifact, not a property
  of these tests, so treat it as flaky-on-cold-cache rather than a known condition.

### Caveat this pass adds

`ruff format` remains **not applied**, for the reason given above: no workflow
invokes it and `ruff format --check tests/` would rewrite the large majority of
the tree. That is a deliberate scope decision, not an oversight.
