# TSR NET — final verification and baseline comparison (tsr-net-01-d5)

Closing report for the NET epic. Produced 2026-08-02 on branch `kanban/tsr-net-01-d5`, a worktree
off `origin/main` at `b5faaad2e`. No source or test file was modified to produce it.

Compares the epic's exit state against the failure baseline established in
[`tsr-net-01-d2-baseline.md`](tsr-net-01-d2-baseline.md) (measured at `0f9544b0a`, 93 commits back).

## Headline

**The NET epic is not finished.** Four of the eighteen failing files were repaired — 12 test
failures resolved, every one of them verified here rather than taken from a commit message. The
other fourteen files fail today with the same counts and the same signatures they had at baseline.

| metric | before (`0f9544b0a`) | after (`b5faaad2e`) |
|--------|---------------------|--------------------|
| files failing | 18 | **14** |
| tests in those 18 files | 278 | 278 |
| passing | 167 | **179** |
| failing/errored | **111** | **99** |

**Of the 14 that remain, 4 are network-attributable** — the acceptance criterion "all
network-attributable failures are resolved" is **not met**, and this report does not claim it is.
The remaining 10 are named below with non-network causes.

## Before / after, per file

`before` is the clean-worktree column of [`tsr-net-01-d2-triage.json`](tsr-net-01-d2-triage.json).
`after` is measured here, same method, same seeded 541-table database.

| file | before | after | outcome |
|------|--------|-------|---------|
| `tests/test_dsyn_emit_ndc.py` | 3p/5f | **8p/0f** | **fixed** |
| `tests/test_dsyn_emit_network.py` | 0p/4f | **4p/0f** | **fixed** |
| `tests/test_network_ingester_e2e.py` | 2p/2f | **4p/0f** | **fixed** |
| `tests/test_ndc_blueprint_route_parity.py` | 1p/1f | **2p/0f** | **fixed** ¹ |
| `tests/ci/test_pr_watcher.py` | 16p/3f | 16p/3f | unchanged |
| `tests/ci/test_pr_watcher_sibling.py` | 5p/4f | 5p/4f | unchanged |
| `tests/e2e_dual_track_lifecycle.py` | 0p/1f | 0p/1f | unchanged |
| `tests/e2e_full_lifecycle_complex.py` | 0p/1f | 0p/1f | unchanged |
| `tests/e2e_ndc_full_lifecycle.py` | 0p/1f | 0p/1f | unchanged |
| `tests/e2e_network_nl_query.py` | 19p/15f | 19p/15f | unchanged |
| `tests/test_advisory.py` | 11p/24f | 11p/24f | unchanged |
| `tests/test_devops_twin_route.py` | 1p/4f | 1p/4f | unchanged |
| `tests/test_federal_peering_request.py` | 33p/1f | 33p/1f | unchanged |
| `tests/test_pdc_routes_engine.py` | 23p/2f | 23p/2f | unchanged |
| `tests/test_pdc_routes_misc.py` | 28p/1f | 28p/1f | unchanged |
| `tests/test_remediation_simulator.py` | 1p/17f | 1p/17f | unchanged |
| `tests/test_terraform_generator.py` | 21p/19f | 21p/19f | unchanged |
| `tests/test_traffic_flow_walkthrough.py` | 3p/6f | 3p/6f | unchanged |
| **total** | **167p/111f** | **179p/99f** | **12 resolved** |

¹ Passes only with `ICDEV_NETWORK_ENABLED=true`. See *The environment confound* below — this file
is the reason that section exists.

**The 14 unchanged files reproduce their baseline counts exactly, to the test.** That is the
strongest evidence available that this measurement is comparable to d2's: 14 independent
files agreeing on both pass and fail counts across 93 commits is not something a mis-seeded
database or a different invocation produces by accident.

## Fixes applied

Three commits account for all 12 resolved failures.

| commit | file(s) | what it did |
|--------|---------|-------------|
| `1216169a6` | `test_dsyn_emit_ndc.py`, `test_dsyn_emit_network.py` | Fixtures patched `get_connection`, which the canvas event emitters no longer import — they write `canvas_events` through `get_canvas_connection()`, so `mock.patch` raised `AttributeError` on every DB-touching test. Replaced the hand-rolled `_ShimConn` classes with `tests/_sql_compat.translating()`. |
| `97459078e` | `test_network_ingester_e2e.py` | Fixture handed `ingest_diagram` a bare `sqlite3` connection; the runtime writes PostgreSQL `%s` placeholders, so every INSERT died on `near "%": syntax error`. Replaced `_UnclosableConnection` with `translating(conn, unclosable=True)`, which delegates rewriting to the same `translate_sql` the runtime uses. |
| `209fa60fd` | `test_ndc_blueprint_route_parity.py` | Re-baselined `tests/fixtures/ndc_route_inventory.json` for `nc_api_export_pptx`, a route added deliberately in ndc-brg-04 one day after the inventory was frozen. Added a docstring paragraph stating when re-baselining is legitimate and when a failure is real drift. |

All three are test-side changes. **No production code was modified to make a test pass** anywhere in
this epic — worth recording, because that was the failure mode the TSR card was written to prevent.

## Remaining failures — every one named

### Network-attributable — 4 files, 46 failures. The epic's unfinished work.

| file | failures | cause |
|------|----------|-------|
| `tests/test_advisory.py` | 24 | Patches `tools.network.advisory._fwd_reach_cache`; the module defines no such name. Either the symbol was renamed and the test never followed, or the test asserts a seam the code never had. Read the module before touching the test. |
| `tests/e2e_network_nl_query.py` | 15 | NL-query routing returns `network_intelligence` where the test expects `failure`. Test and classifier disagree on a contract; decide which is right rather than deleting the assertion. |
| `tests/test_traffic_flow_walkthrough.py` | 6 | `sqlite3.OperationalError: near "%": syntax error` — the second and last file in family B. Mechanically identical to the `test_network_ingester_e2e.py` fix already merged in `97459078e`; `tests/_sql_compat.translating()` already exists. **This is the cheapest remaining fix in the epic.** |
| `tests/test_federal_peering_request.py` | 1 | `pmc_request_id` never set — one assertion in an otherwise green 34-test file. |

### Non-network causes — 10 files, 53 failures.

| file | failures | non-network cause |
|------|----------|-------------------|
| `tests/e2e_dual_track_lifecycle.py` | 1 | **Not a pytest module.** Calls `sys.exit()` at module scope; pytest raises it through collection as `SystemExit`, which `--continue-on-collection-errors` cannot catch. A harness-shape defect, not a subsystem defect. See [`tsr-net-01-d2-slice-runnability.md`](tsr-net-01-d2-slice-runnability.md). |
| `tests/e2e_full_lifecycle_complex.py` | 1 | Same — module-scope `sys.exit()`. |
| `tests/e2e_ndc_full_lifecycle.py` | 1 | Same — module-scope `sys.exit()`. |
| `tests/test_terraform_generator.py` | 19 | `jinja2.UndefinedError: 'var' is undefined` — an IaC template rendered under `StrictUndefined` against a context that never supplies `var`. Terraform codegen, not networking. |
| `tests/test_remediation_simulator.py` | 17 | `KeyError: 'action_id'` — the remediation simulator's action dict lost or renamed a key. Self-healing subsystem. |
| `tests/ci/test_pr_watcher.py` | 3 | Assertion drift on the PR-watcher's gate message (`'refusing auto-merge'` vs `'enforced gate: verification unreadable'`). `tools/ci`, not `tools/network`. |
| `tests/ci/test_pr_watcher_sibling.py` | 4 | Assertion drift: sibling-branch policy returns `wait` where the test expects `merge`. `tools/ci`. |
| `tests/test_devops_twin_route.py` | 4 | `no such table: pdc_snapshots` — a PDC canvas table created by a canvas-local `db/init_db.py` the test never invokes. d2 warns a second, independent `nav_tree` template defect sits underneath; budget two fixes. |
| `tests/test_pdc_routes_engine.py` | 2 | Pipeline-jobs assertion drift in the PDC route engine. |
| `tests/test_pdc_routes_misc.py` | 1 | `assert 0 == 1`. **Symptom is ambient-data-dependent** — 0 on an empty DB, 35 in the long-lived shared checkout. Whoever fixes it must decide whether the test should seed its own row or filter; do not fix it against the shared checkout alone. |

## The environment confound — a finding in its own right

The first pass of this verification measured `test_ndc_blueprint_route_parity.py` at **0p/2f**, worse
than its 1p/1f baseline, on `AssertionError: create_network_blueprint returned None (disabled?)`.

That was not a regression. The kanban dispatch environment exports:

```
ICDEV_NETWORK_ENABLED=false
ICDEV_NDC_ENABLED=false
ICDEV_STORAGE_BACKEND=postgresql
ICDEV_DASHBOARD_URL=http://127.0.0.1:5090
```

`create_network_blueprint()` defaults to enabled and returns `None` only when the variable is
explicitly falsy, and the test guards with `os.environ.setdefault("ICDEV_NETWORK_ENABLED", "true")`
— which **cannot** override a value that is already present. So the canvas was switched off
underneath a test whose whole purpose is to enumerate that canvas's routes. Re-run with the toggle
pinned true, the file passes 2/2.

Two things follow, both worth carrying beyond this epic:

1. **`os.environ.setdefault` is not a test guard.** It expresses "if nobody has an opinion, use
   this", but a test that needs a canvas enabled has an opinion. `monkeypatch.setenv` is the
   correct tool. This pattern will misfire in any harness that exports canvas toggles.
2. **Pin canvas toggles when measuring a canvas slice.** The d2 baseline ran in a shell without
   these exports, so the confound is invisible in a single run and only appears as an unexplained
   delta when two runs are compared. Every NET-slice measurement should export
   `ICDEV_NETWORK_ENABLED=true ICDEV_NDC_ENABLED=true` alongside the `ICDEV_STORAGE_BACKEND=sqlite`
   pin d2 already mandates.

## Method

Identical to the d2 baseline so the numbers compare. Fresh worktree off `origin/main`, seeded with
the three steps the TSR card mandates:

```bash
export PYTHONPATH='C:\AI\.wt-tsh-d4-audit5\.tmp\worktrees\tsr-net-01-d5'
export ICDEV_STORAGE_BACKEND=sqlite      # else the seed half-lands against an unreachable PG
python tools/db/init_icdev_db.py                                     # 525 tables
python tools/studio/init_db.py                                       # studio tables
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py  # Migration 311
```

541 tables — the same count d2 measured against, which is part of why the 14 unchanged files
reproduce exactly.

One pytest process per file, counts read back from JUnit XML rather than scraped from stdout:

```
python -m pytest <file> -p no:cacheprovider --tb=no -q \
    --continue-on-collection-errors --timeout=90 --timeout-method=thread \
    --junit-xml=<file>.xml
```

### On the "stash / unstash" step in the card

The card specifies stashing local changes to measure `before`, then unstashing for `after`. **There
were no local changes to stash** — every NET fix was merged to `main` through a PR before this card
ran, and the worktree is clean at `b5faaad2e`. The step is a no-op here.

It was also not the right instrument. `git stash` is shared across all worktrees of a repository,
and several kanban sessions run concurrently against this one — a stash pushed here surfaces in
another session's `git stash list` and can be popped by it. `before` is therefore taken from the d2
triage JSON, which is a *measured* artifact committed to the repo, not an estimate. The 14 unchanged
files reproducing their baseline counts exactly is what validates using it.

### Scope measured

The 18 files the d2 baseline classified as real defects — the only files in the 131-file slice that
could change state. The 113 files clean at baseline were **not** re-run; that is the one gap in this
report, stated rather than papered over. 83 of them import a module touched by the 93 commits since
baseline and are therefore a live regression risk. Re-running them is a follow-up worth carding, not
a claim this report makes.

## Lint

`ruff check` is clean on every file the epic's three fix commits touched:

```
tests/test_network_ingester_e2e.py      tests/test_ndc_blueprint_route_parity.py
tests/test_dsyn_emit_ndc.py             tests/test_dsyn_emit_network.py
tests/test_dsyn_emit_cloudforge.py      tests/_sql_compat.py
tests/test_coherence_checker.py         tools/workflow/coherence_checker.py
icdev/tools/workflow/coherence_checker.py
```

→ `All checks passed!`

## Recommended next cards

1. **`test_traffic_flow_walkthrough.py`** — family B, the pattern is already merged and proven in
   `97459078e`. Cheapest remaining fix in the epic.
2. **`test_advisory.py`** (24 failures, the largest single block) — read `tools/network/advisory`
   first and decide rename-vs-missing-seam before editing the test.
3. **`e2e_network_nl_query.py`** — adjudicate the classifier contract.
4. **`test_federal_peering_request.py`** — one assertion.
5. **Regression sweep** over the 83 at-risk clean-both files.
6. **Harness card, epic-independent** — the three `sys.exit()` scripts abort any pytest session that
   collects them. They are counted as failures here and in d2, but they are not subsystem defects
   and no NET fix can resolve them.

## Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-net-01-d5-final-report.md` | this document |
| `docs/testing/tsr-net-01-d5-after.json` | per-file after counts, return codes and first-failure signatures |
| `docs/testing/tsr-net-01-d2-triage.json` | the before baseline, from tsr-net-01-d2 |
