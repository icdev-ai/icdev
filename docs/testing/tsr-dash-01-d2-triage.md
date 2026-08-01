# TSR DASH — failing-test triage: clean worktree vs populated checkout (tsr-dash-01-d2)

Diagnostic only. No source file was modified. Produced 2026-08-01 on branch
`kanban/tsr-dash-01-d2`, off `origin/main` at `3c2650eb7`.

The question this answers, per test file in the DASH slice:

| category | meaning | tool label |
|---|---|---|
| **(a)** | passes on the populated checkout, fails clean → the fixture depends on ambient DB state | `ambient` |
| **(b)** | fails in **both** places → a real defect that reproduces from a cold start | `real` |
| **(c)** | anything else — fails only on the populated checkout (stale-row contamination), not runnable headless, or not executed | `shared`, `unknown`, `not-run` |

## 1. The two arms

Both arms are the **same commit** (`3c2650eb7`). The only difference is `data/`.

| arm | path | `data/icdev.db` |
|---|---|---|
| clean worktree | `C:\AI\.worktrees\tsr-dash-d2` | seeded schema only — `tools/db/init_icdev_db.py`, `tools/studio/init_db.py`, migration 311, per the tsr-dash-01-d1 recipe |
| populated | `C:\AI\ICDev\.tmp\worktrees\tsr-dash-01-d2-pop` | full copy of the shared checkout's `data/` — months of accumulated dashboard and runner traffic |

**The populated arm is a worktree carrying a copy of the shared checkout's `data/`, not
`C:\AI\ICDev` itself.** Running a 158-file pytest slice directly in the shared checkout would
write to the live `data/icdev.db` that other concurrent sessions and the kanban scheduler are
using. The copy gives the same ambient-state condition without that blast radius.

Command, identical in both arms:

```bash
export ICDEV_STORAGE_BACKEND=sqlite          # required; without the pin the seed half-lands
export PYTHONPATH=<arm root>
python -m pytest $(cat runlist.txt) -q -rfE --timeout=60 \
    --continue-on-collection-errors -p no:cacheprovider
```

`-rfE` (not `-rf`) is deliberate: `-rf` hides collection ERRORs, and in this slice collection
errors are a large share of the failures.

## 2. Scope: 189 slice files → 158 executed, 31 excluded

The DASH slice from tsr-dash-01-d1 is 189 files. **31 were excluded from execution** and are
categorised **(c) not runnable headless** without being run:

- 26 under `tests/e2e_selenium/`, plus `tests/e2e_fathomdesk_modal.py`,
  `tests/e2e_infra_emit.py`, `tests/e2e_network_canvas.py`,
  `tests/e2e_zta_lac_deny_assertions.py` — all drive a real browser through Selenium/WebDriver
  against a dashboard on `localhost:5050`. With no server and no browser they fail identically in
  both arms for a reason that has nothing to do with DB state, so running them would add 31
  false `real` verdicts.
- `tests/e2e/e2e_govlift_lifecycle.py` — not a browser test, but its filename matches neither
  `test_*.py` nor `*_test.py`, so **pytest never collects it**. It is dead weight in the slice.

Excluded files are listed verbatim in `tsr-dash-01-d2-excluded.txt`. The remaining **158**
executed files are in `tsr-dash-01-d2-runlist.txt`.

## 3. Results — INCOMPLETE, and why

**The a/b split is not in this document.** Both arms were launched and both were still running at
~17% of the slice when the session's dispatch budget expired. This is the fourth attempt on this
task and the previous three were killed the same way, so the cause is recorded here rather than
retried a fifth time unchanged.

**Measured throughput:** ~10 s per 1% of the slice, per arm, with both arms running concurrently.
That is **≈33 minutes per arm** for 158 files — against a 900 s (15 min) dispatch budget. Roughly
60 s of each arm is collection alone (158 files, heavy transitive imports). *No* single-session
run of the whole slice can finish. The task as scoped does not fit one dispatch and must be
sharded.

### What the partial run does establish

Up to the 17% mark the two arms are **byte-for-byte identical in their progress output** — the
same `F` at the same offset, the same three `E`s at the same offset, and the same run of 18–22
consecutive `E`s in the segment both arms were executing when the budget ran out.

That is evidence, though not proof, that the DASH failures are dominated by category **(b)
real** — failures that reproduce from a cold start and are indifferent to ambient DB state. Had
category (a) dominated, the clean arm would have shown failures where the populated arm showed
passes, and the two dot-streams would have diverged. They did not diverge anywhere in the first
17%. The long consecutive `E` runs are collection-time errors (import/fixture resolution), which
by construction cannot depend on DB rows.

**This is a directional read on the first 17%, not a verdict on the slice.** The per-file a/b/c
table with error messages — the actual acceptance criterion — is still owed.

### Decomposition — five shards that each fit a dispatch

The slice partitions cleanly by test-name prefix. Each shard below is sized to finish both arms
inside one dispatch, and each is independently reportable:

| shard | selector | files | est. per arm |
|---|---|---|---|
| DASH-1 | `tests/browser/**`, `tests/dashboard/**`, `tests/viz/**`, `tests/slides/**`, `tests/genesis_auto/**`, `tests/tools/**` | 26 | ~6 min |
| DASH-2 | `tests/test_gcpl_*` (CPMP/govcon contract lifecycle) | 35 | ~7 min |
| DASH-3 | `tests/test_nav_*` (NAV readiness sweep) | 30 | ~7 min |
| DASH-4 | `tests/test_iqe_*` | 24 | ~5 min |
| DASH-5 | remainder — `tests/test_proposals_*`, `tests/test_chat_*`, and 31 unprefixed root files | 43 | ~8 min |

26 + 35 + 30 + 24 + 43 = 158. The lists are committed as
`tsr-dash-01-d2-shard{1..5}.txt`. (`tests/dashboard/test_nav_misc_03_route_extraction.py` sorts
into DASH-1 by directory, not into DASH-3 by name.)

Both arms and the `tsr_triage_diff.py` invocation are unchanged per shard; only `--slice`
changes. The shard tables concatenate into the deliverable this task asked for.

**Recommendation: split tsr-dash-01-d2 into five sibling tasks on the DASH epic, one per shard.**
The two-arm harness, the seeded clean worktree, the populated worktree, and the file lists are all
built and committed — a shard task starts from `python -m pytest $(cat <shard>) …` and nothing else.

## 4. How to re-run

```bash
python tools/testing/tsr_triage_diff.py \
    --shared docs/testing/tsr-dash-01-d2-populated.txt \
    --clean  docs/testing/tsr-dash-01-d2-clean.txt \
    --slice  docs/testing/tsr-dash-01-d2-runlist.txt \
    --out    docs/testing/tsr-dash-01-d2-table.md
```
