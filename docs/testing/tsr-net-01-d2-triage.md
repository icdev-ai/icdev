# TSR NET — baseline triage: clean worktree vs shared checkout (tsr-net-01-d2)

Diagnostic only. No source or test file was modified to produce this report.

Status: **method + runlist landed; per-file counts pending** — see "Run status" below.

## What is being compared

| | clean worktree | shared checkout |
|---|---|---|
| path | `C:\AI\ICDev\.tmp\worktrees\tsr-net-01-d2` | `C:\AI\ICDev` |
| commit | `c96909515` | `c96909515` |
| branch | `kanban/tsr-net-01-d2-triage` | `main` |
| `data/icdev.db` | 8.9 MB, seeded fresh by the `tsr-net-01-d1` three-step recipe | 1.8 GB, accumulated shared state |

The two runs differ only in cwd and `PYTHONPATH`. Everything else — pytest flags, file list,
`ICDEV_STORAGE_BACKEND=sqlite`, `ICDEV_DB_PATH` unset — is identical:

```bash
export ICDEV_STORAGE_BACKEND=sqlite
unset ICDEV_DB_PATH
pytest $(cat docs/testing/tsr-net-01-d2-runlist.txt | tr '\n' ' ') \
  -q --tb=no -p no:cacheprovider --timeout=90 --timeout-method=thread \
  --continue-on-collection-errors --junitxml=<out>.xml
```

## Finding 1 — the NET slice cannot be run as published

`docs/testing/tsr-net-01-slice.txt` (131 files, from `tsr-net-01-d1`) contains **5 files that are
executable scripts, not pytest modules**. They call `sys.exit()` at module scope, which pytest's
assertion-rewriting importer raises through collection as `SystemExit`. That is not a collectable
error — it aborts the entire session with `INTERNALERROR`, so **zero tests run**:

```
INTERNALERROR>   File "C:\AI\ICDev\tests\e2e_dual_track_lifecycle.py", line 535, in <module>
INTERNALERROR>     sys.exit(0 if FAIL == 0 else 1)
INTERNALERROR> SystemExit: 1
no tests ran in 76.26s
EXIT=3
```

`--continue-on-collection-errors` does not help; `SystemExit` escapes the collection-error handler.

The five files:

```
tests/e2e_devops_twin.py
tests/e2e_dual_track_lifecycle.py
tests/e2e_full_lifecycle_complex.py
tests/e2e_ndc_full_lifecycle.py
tests/e2e_network_nl_query.py
```

Note the asymmetry that made this worth recording: the **same command aborted in the shared
checkout and did not abort in the clean worktree**. `e2e_dual_track_lifecycle.py` exits non-zero
only when its own `FAIL` counter is non-zero, and that counter depends on the accumulated state in
the shared 1.8 GB `data/icdev.db`. So the shared checkout's local state does not merely change a
few test outcomes — it can decide whether the suite runs at all.

**Consequence for the epic:** the runnable NET slice is 126 files, published here as
`docs/testing/tsr-net-01-d2-runlist.txt`. Anyone re-running `tsr-net-01-slice.txt` verbatim will get
`no tests ran` and may mistake it for a clean tree.

## Run status

Both baseline runs were launched from this session:

| run | file list | outcome |
|-----|-----------|---------|
| shared checkout, 131-file slice | `tsr-net-01-slice.txt` | **aborted** — `INTERNALERROR` / `no tests ran` (Finding 1) |
| clean worktree, 131-file slice | `tsr-net-01-slice.txt` | completed collection, executed to ~42% |
| shared checkout, 126-file runlist | `tsr-net-01-d2-runlist.txt` | relaunched |

Per-test outcomes are captured to JUnit XML and classified by
`docs/testing/tsr-net-01-d2-triage.json` into four buckets:

| bucket | meaning |
|--------|---------|
| `fail-both` | **real defect** — broken in both environments; environment is not the cause |
| `pass-shared/fail-clean` | **missing seed / fixture / local state** in the clean worktree |
| `pass-clean/fail-shared` | **shared-checkout contamination** — stale local state breaks a test that is green on a clean tree |
| `pass-both` | already working |

`tests/e2e_*.py` outcomes are excluded from both sides of the comparison so the two runs cover an
identical file set.

## Reproducing

```bash
cd C:/AI/ICDev/.tmp/worktrees/tsr-net-01-d2
export PYTHONPATH='C:\AI\ICDev\.tmp\worktrees\tsr-net-01-d2'
export ICDEV_STORAGE_BACKEND=sqlite
unset ICDEV_DB_PATH
pytest $(cat docs/testing/tsr-net-01-d2-runlist.txt | tr '\n' ' ') \
  -q --tb=no --timeout=90 --continue-on-collection-errors
```

Swap `cd`/`PYTHONPATH` for `C:\AI\ICDev` to get the shared-checkout side.
