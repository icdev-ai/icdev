# tsr-gen-01-d5 — Final verification and ruff report (GEN epic)

Closing card for the GEN epic of the TSR test-suite remediation. Verifies the
combined effect of `tsr-gen-01-d3` (PRs #1101 / #1102 / #1118) and
`tsr-gen-01-d4` (PR #1179), both merged to `main`.

> **Report location.** The card names `/tmp/final_report.md`. That path is not
> usable here — in Git Bash `/tmp` is the MSYS temp dir while Python resolves it
> to `C:\tmp`, so the two halves of a handoff silently read different files.
> The report lives in the repo next to the d3/d4 docs instead.

## Method

Two worktrees created from the same `origin/main` commit (`faafbd679`), seeded
identically and run under identical ambient host conditions.

* **after** — `origin/main` as merged.
* **before** — `origin/main` with only the six touched test files restored to
  their pre-TSR content (`git restore --source=0727e8467^` for the d3 files,
  `--source=ed6eefcbd^` for the d4 files). Everything else is byte-identical.

`git stash` was deliberately **not** used to build the baseline, even though the
card suggests it: the stash stack is shared across all worktrees of a repo, and
several other sessions are active in this checkout.

Both DBs seeded with the backend pinned, and verified — **541 tables / 16
`studio_*`** in each:

```bash
PYTHONPATH=<wt> ICDEV_STORAGE_BACKEND=sqlite python tools/db/init_icdev_db.py
PYTHONPATH=<wt> ICDEV_STORAGE_BACKEND=sqlite python tools/studio/init_db.py --json
PYTHONPATH=<wt> ICDEV_STORAGE_BACKEND=sqlite python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

Ambient conditions matched those recorded in the d4 doc: dashboard answering
`200` on `127.0.0.1:5050`, `.tmp/kanban_scheduler.pid` present, and
`KANBAN_RUBRIC_LOOP=true` exported into the test process.

One pytest process **per file** — a module-scope `sys.exit()` anywhere in the
slice would otherwise abort the whole session and be misread as a mass failure.

## Results

Slice: 6 files, 78 tests.

| File | Before | After |
|---|---|---|
| `tests/test_kanban_silent_cleanup.py` | 1 failed, 5 passed | **6 passed** (17.2s) |
| `tests/test_nova_sela_evolution.py` | 3 failed, 6 passed | **9 passed** (2.3s) |
| `tests/test_value_scorer_and_bulk_move.py` | 7 failed, 24 passed | **31 passed** (8.2s) |
| `tests/genesis/test_kanban_message_injection.py` | 2 failed, 4 passed | **6 passed** (3.2s) |
| `tests/test_kanban_manual_gate_exemption.py` | 2 failed, 11 passed | **13 passed** (159.3s) |
| `tests/test_qa_agent_reflex.py` | **timeout @180s**, 0 completed | **13 passed** (2.3s) |

**Files in slice: 6. Tests passing before: 50. Tests passing after: 78.**
15 failed and 13 never ran before; 0 failed and 0 unrun after.

The before-column failures reproduce the causes the d3/d4 docs claim, which is
the point of re-running them — `%s` placeholders reaching a raw `sqlite3`
connection (`sqlite3.OperationalError: near "%": syntax error`) for the d3 files,
and ambient host state for the d4 files (`KANBAN_RUBRIC_LOOP` short-circuiting
dispatch, the scheduler pidfile making the reaper no-op, and a live dashboard on
`localhost:5050` hanging `test_qa_agent_reflex` inside `sock.connect`).

## Remaining failures

**None.** No test in the slice fails, and no failure is left unexplained.

## One caveat, with a confirmed cause — not a failure

`tests/test_kanban_manual_gate_exemption.py` passes 13/13 but is a wall-clock
hazard: across four runs it completed in 101s and 159s twice, and exceeded a
180s and a 540s cap under concurrent load. **No assertion ever failed** — every
non-completion was the cap expiring during fixture setup.

The cause is measured, and it is not in the TSR files. The shared
`tests/conftest.py::icdev_db` fixture is function-scoped and rebuilds
`MINIMAL_ICDEV_SCHEMA` — 137,655 chars, 253 `CREATE TABLE` statements — for
every test. On this host that single `executescript` costs **31–51 seconds**,
and the file has 9 tests that request the fixture.

The cost is entirely per-statement fsync:

| `sqlite3.connect` target | `executescript(MINIMAL_ICDEV_SCHEMA)` |
|---|---|
| tmp file, default pragmas | **51.24s** |
| tmp file, `synchronous=OFF` + `journal_mode=MEMORY` | **0.05s** |
| `:memory:` | **0.02s** |

A ~1000x difference on a throwaway `tmp_path` database whose durability across a
power loss is meaningless. Two pragmas in the `icdev_db` fixture would reclaim
roughly 450s from this one file alone, and the fixture is used well beyond the
GEN slice.

That change is **not** made here — it is a shared fixture touching a large part
of the suite, so it belongs in its own card with its own verification, not in a
GEN-epic closing PR. Recommended as follow-up.

## ruff

`ruff check` — clean, using the exact command CI enforces
(`.github/workflows/icdev-ci.yml:36`):

```
python -m ruff check tests/ --select E,F,W --ignore E402,E501,E701,E702,E721,E722,E731,E741,F404
All checks passed!
```

All six files pass, as does `tests/` as a whole. **The slice is ruff-clean.**

`ruff format` was run in `--diff` mode and **deliberately not applied.** It
reports all six files would be reformatted (336 lines of churn), but this repo
does not use the formatter:

* CI runs `ruff check` only — no workflow invokes `ruff format --check`.
* `ruff.toml` sets `line-length = 120` and ignores `E501`; nothing enforces
  formatting.
* `ruff format --check tests/` reports **1688 of 2059 files would be
  reformatted** — 82% of the test suite.

Reformatting six files out of 2059 would buy no gate compliance while making
those files inconsistent with the 1682 others and adding needless churn to known
merge-conflict hotspots. Adopting `ruff format` is a repo-wide decision; if it is
wanted, it should land as one sweep, not as drive-by edits inside a test-fix PR.

## Reproduce

```bash
git worktree add --detach <before> origin/main
git -C <before> restore --source=0727e8467^ -- \
  tests/test_kanban_silent_cleanup.py tests/test_nova_sela_evolution.py \
  tests/test_value_scorer_and_bulk_move.py
git -C <before> restore --source=ed6eefcbd^ -- \
  tests/genesis/test_kanban_message_injection.py \
  tests/test_kanban_manual_gate_exemption.py tests/test_qa_agent_reflex.py
# seed both worktrees as above, then one pytest process per file
```
