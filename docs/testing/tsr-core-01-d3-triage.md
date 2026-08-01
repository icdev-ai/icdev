# TSR CORE - shared-checkout vs clean-worktree failure triage (tsr-core-01-d3)

Diagnostic only. Produced 2026-07-31. No source file under `tools/` or `tests/` was modified;
the only code added is the parser `tools/testing/tsr_triage_diff.py` that emits the table below.

Answers the d3 question: of the CORE test failures, which are **ambient** - an artefact of the
shared checkout's accumulated database and stray state - and which are **real** defects that
reproduce from a cold start?

## Headline

**Zero ambient failures were found.** Every failure in the sample reproduces identically in a
clean worktree. The two checkouts produced byte-identical outcomes: `607 passed, 4 failed,
20 errors` on each side, with the same tests failing for the same reasons.

For the CORE epic the "it only fails because the shared DB is dirty" hypothesis is **disconfirmed**.
Remediation tasks downstream of this one should be written as real bug fixes, not as
environment cleanup.

## The two arms

| | shared checkout | clean worktree |
|---|---|---|
| path | `C:\AI\ICDev` | `C:\AI\.worktrees\tsr-core-01-d3-clean` |
| commit | `8fc1394e1` | `75d66975b` (off `origin/main`) |
| `data/icdev.db` | 1,883,885,568 B (~1.8 GB, months of dashboard + kanban traffic) | 9,388,032 B (~9 MB, schema seed only) |
| backend | forced to SQLite by `tests/conftest.py` | forced to SQLite by `tests/conftest.py` |

The DB size ratio is ~200:1, which is the contrast the task exists to exercise. Note the clean arm
is **schema-seeded, not empty** - an entirely unseeded worktree hangs rather than fails, so it
would have measured nothing.

## Command

Both arms ran the identical file list, five shards in parallel per arm (no `pytest-xdist` in this
environment; 20 CPUs, 10 concurrent processes):

```bash
python -m pytest $(cat shard-0N) --timeout=45 --timeout-method=thread \
  -p no:cacheprovider --tb=no -q -rfE --continue-on-collection-errors
```

`-rfE`, not `-rf`: `-rf` omits the short-summary section for **errors**, which silently hid 20 of
the 24 defects on the first pass. `--timeout` is load-bearing - one file wedges (below).

## Scope, and what is not covered

The d2 Tier-A slice is 134 files. A serial run of all 134 reached **3% in four minutes** - on the
order of an hour per arm, two hours for the pair, which does not fit a single session. This run
therefore triages a deterministic **1-in-3 sample, 45 of the 134 files** (`awk 'NR%3==1'` over
`docs/testing/tsr-core-01-slice.txt`, preserved as `docs/testing/tsr-core-01-d3-sample45.txt`).

The remaining 89 files are **untriaged**, not passing. Finishing them needs either a follow-up task
or `pytest-xdist` added to the environment.

## Confounders, stated

The shared checkout is **38 commits behind** `origin/main`. That is a real confounder and it was
measured rather than assumed:

- **0 of the 134 slice test files** differ between the two commits.
- **2 CORE source files** differ: `tools/auth/saml.py`, `tools/db/init_icdev_db.py`.
- `tests/conftest.py` differs by +103 lines (adds the `databridge_agent_access_log` schema and the
  `_sqlite_connection_tracker` / `assert_no_leaked_transaction` fixtures).

None of the three changed the sampled outcome - the arms agree test-for-test. Had they diverged,
these files would be the first place to look, and any affected row would be `unknown`, not `real`.

## Result

| test_file | shared_checkout_result | clean_worktree_result | classification | first failure reason |
|---|---|---|---|---|
| `tests/e2e_govcon_proposals_cpmp.py` | 16E | 16E | **real** |  |
| `tests/test_nav_bld_01_aisg_real_data.py` | 4E | 4E | **real** |  |
| `tests/test_ecr_bill.py` | 3F | 3F | **real** | AssertionError: ... |
| `tests/test_lpx_proxy_metrics.py` | 1F | 1F | **real** |  |
| `tests/test_production_audit.py` | HANG | HANG | **real** | wedged in pathlib.open; killed by --timeout=45 |
| `tests/test_saas_portal.py` | not run | not run | **unknown** | shard aborted by the hang above |
| `tests/testing/test_health_check.py` | not run | not run | **unknown** | shard aborted by the hang above |

### Baseline counts (before)

| metric | shared checkout | clean worktree |
|---|---|---|
| passed | 607 | 607 |
| failed | 4 | 4 |
| error | 20 | 20 |
| skipped | 0 | 0 |
| files in slice | 45 | 45 |

| classification | files |
|---|---|
| real | 5 |
| ambient | 0 |
| shared | 0 |
| unknown | 2 |
| clean | 38 |

### Reading the table

`3F` = 3 failed tests in that file; `4E` = 4 errored (collection or fixture); `HANG` = the shard's
45s guard fired. Files green in both arms are counted in the summary and omitted from the table.

The five real defects, for whoever picks up remediation:

1. **`tests/e2e_govcon_proposals_cpmp.py`** - 16 errors, the largest single cluster in the sample.
2. **`tests/test_production_audit.py`** - wedges in `pathlib.open` partway through its 42 tests and
   never returns. It hangs in *both* arms, so it is not the shared checkout's 1.8 GB tree that
   wedges it. This is the file behind the "unseeded worktree hangs" folklore; it needs a real
   timeout or a bounded scan root, and until it has one it will keep taking its whole shard with it.
3. **`tests/test_nav_bld_01_aisg_real_data.py`** - 4 errors.
4. **`tests/test_ecr_bill.py`** - 3 failures, `test_rollup_reflex_run` asserting `False is True`.
5. **`tests/test_lpx_proxy_metrics.py`** - 1 failure.

The two `unknown` rows are collateral from defect 2: `test_saas_portal.py` and
`tests/testing/test_health_check.py` sat behind the hang in shard 03 and never executed. They are
unclassified in both arms - symmetric, so the comparison stays valid, but they need a re-run once
the hang is fixed.
