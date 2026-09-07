# mfx-own-04 — worktree husk survey

**Measured:** 2026-09-06, live roots on the operator host, live PG board.
**Re-derive:** `python -m tools.kanban.worktree_husks --survey --json`
**Tool:** `tools/kanban/worktree_husks.py` (the shipped predicate; the numbers
below are its output, not a second script's).

## The question

A directory at `.tmp/worktrees/<task-id>` with **no `.git` marker** and **no
entry in `git worktree list`** is a HUSK. How many exist, how old, how big, and
does any belong to a task still `in_progress`? A class that would sweep a live
task's directory is wrong and must be narrowed before arming.

## Why the 7-day rule could not reach them

The card said the gap was the clock. Re-reading the sweeper, it is the clock
AND the candidate set:

- `_sweep_candidates` returns only directories carrying `.git`. A husk is never
  a candidate, so no clock applies to it at all.
- `_worktree_is_disposable` refuses "N entries but no .git -- possibly a partial
  delete" by design (kpr-dup-10), so even a husk that reached it would be kept.
- The empty-checkout requeue proof refuses `worktree_unregistered` on the same
  directory (kpr-stale-06), correctly: `git status` inside an unregistered
  directory describes the ENCLOSING checkout.

Between the three, task-det-e9a2e3ea16 sat in `validating` for 2.7 days while
`kanban_requeue_reflex` reported 60 runs, 60 successes, metric 0.0.

## In scope: `.tmp/worktrees` (28 .git-less direct children)

| task id | board status | newest content age | size | verdict |
|---|---|---|---|---|
| data | (no row) | — | — | refused `no_board_row` |
| tools | (no row) | 0.51 d (still being written) | 1.1 MB | refused `no_board_row` |
| flx-airgap-01 | done | 33.3 h | 0 | proven (empty) |
| flx-az-01 | done | 28.3 h | 0 | proven (empty) |
| flx-bridge-01 | done | 33.6 h | 0 | proven (empty) |
| flx-ci-02 | done | 32.0 h | 0 | proven (empty) |
| flx-gen-01 | done | 34.6 h | 0 | proven (empty) |
| flx-seam-02 | done | 36.0 h | 536.3 MB | proven |
| flx-sim-01 | done | 34.2 h | 0 | proven (empty) |
| flx-twin-01 | done | 30.6 h | 0 | proven (empty) |
| ftl-chain-01 | done | 280.3 h | 0.2 MB | proven (`.logs` only) |
| ftl-mom-02 | done | 298.1 h | 0 | proven (empty) |
| ftl-trend-02 | done | 282.5 h | 0 | proven (empty) |
| mfx-ci-01 | done | 40.6 h | 126.1 MB | proven (`node_modules` only) |
| mfx-sib-01 | done | 34.6 h | 0 | proven (empty) |
| qa-fail-1474518ac97ac6a2 | done | 257.3 h | 548.3 MB | proven |
| qa-fail-a5dbf266dfb0ce4a | done | 276.4 h | 541.6 MB | proven |
| rmf-inert-01 | done | 87.5 h | 0 | proven (empty) |
| **task-det-e9a2e3ea16** | **validating** | 66.6 h | 534.4 MB | proven — the card's case |
| task-e2e-1290dda3 | done | 176.1 h | 126.1 MB | proven (`node_modules` only) |
| task-e2e-64483d48 | done | 176.1 h | 126.1 MB | proven (`node_modules` only) |
| task-e2e-6c18f678 | done | 191.6 h | 813.7 MB | proven |
| task-e2e-7a938cca | done | 306.9 h | 537.9 MB | proven |
| task-e2e-9970bcad | done | 273.5 h | 661.6 MB | proven |
| task-e2e-c5bf8705 | done | 224.8 h | 532.1 MB | proven |
| task-qa-sweep-832c0874 | done | 176.1 h | 126.1 MB | proven (`node_modules` only) |
| task-qa-sweep-9c6fc8fe | done | 325.2 h | 665.7 MB | proven |
| task-qa-sweep-c17bd3d6 | done | 181.0 h | 126.1 MB | proven (`node_modules` only) |

Totals:

| | |
|---|---|
| proven husks | 26 |
| proven size | 6,002 MB |
| refused | 2 (`no_board_row`) |
| unmeasurable | 0 |
| tasks `in_progress` | **0** |
| tasks terminal (`done`) | 25 |
| youngest proven husk | 28.3 h |
| oldest proven husk | 325.2 h (13.55 d) |

Not one candidate belonged to a live task. The two refusals are the point of
the board-row guard: `.tmp/worktrees/data` and `.tmp/worktrees/tools` are not
worktrees of anything — they are residue of a module computing the repo root
from its own location while running inside a worktree (the xit-decl-03 class of
defect), and `tools/presentations/templates_uploaded/` was written to 12 hours
before the survey. A name-based class with no board check would have deleted a
directory something is still writing.

The `node_modules`-only and `.logs`-only rows are what an earlier `rmtree` with
`ignore_errors=True` leaves behind on Windows: the read-only files and the
paths past MAX_PATH survive, the `.git` file does not. The act's own rmtree
clears read-only bits and uses the extended-length prefix for that reason.

## Walk cost

The age is the newest mtime in the WHOLE tree, because on Windows the top-level
directory stamp moves only when a direct child changes and a process still
writing `.logs/*.ndjson` deep inside is the one sign of life a husk can show.
Measured per husk: 13–52 s for a 20–29k-file tree, 0.3–3.7 s for a
`node_modules`-only residue, 0 s for an empty one. That cost is why the sweep is
bounded at `max_removals_per_run: 3` (oldest first, the rest deferred BY NAME)
and why a walk over `max_walk_entries` / `walk_budget_seconds` refuses as
`age_unmeasurable` rather than trusting a partial walk — a partial walk yields a
LOWER bound on the newest mtime, i.e. an OVER-estimate of the age, the one
direction that errs toward deleting.

## Out of scope: the sanctioned root (`%TEMP%/icdev-worktrees`), 14 husk-shaped directories

Under the nested layout a directory is named for a slug or a session, not a
task id. An invented id matches no task, and an id that matches nothing
silently defeats the `in_progress` guard — so nothing here is acted on. They
are reported so the next card can decide what evidence WOULD let them be.

| path (under the root) | entries | top-level age |
|---|---|---|
| cli/adhoc/data | 1 | 691.6 h |
| cli/adhoc/ftl-live | 1 | 287.2 h |
| cli/adhoc/ftl-val2 | 1 | 288.4 h |
| cli/adhoc/qt-e2e-demo | 1 | 334.4 h |
| cli/adhoc/qt-equity-autotrader | 1 | 334.6 h |
| cli/adhoc/tools | 1 | 690.1 h |
| cli/kanban-scheduler/data | 1 | 670.8 h |
| cli/kanban-scheduler/tools | 1 | 670.6 h |
| cli/local-026f5ed1fc5c | 1 | 175.9 h |
| cli/local-2ad8368cdc06/data | 1 | 492.7 h |
| cli/local-2ad8368cdc06/tools | 1 | 492.7 h |
| verify/data | 1 | 613.8 h |
| verify/redfirst-39b47c40ecba-2384 | 40 | 101.5 h |
| verify/tools | 1 | 614.2 h |

Nine of the fourteen are `data` / `tools` — the same root-computing residue as
under `.tmp/worktrees`, now at three different session levels. One
(`verify/redfirst-39b47c40ecba-2384`) is a real husk of a red-first verify
checkout; it has no task id and stays.

## The clock

`husk_age_hours: 6`. A live worktree always has `.git`, so no age is needed to
protect one. The clock bounds the two in-flight states that briefly look like a
husk: the interval inside `git worktree add` between `mkdir` and the `.git`
write (sub-second), and a partial `rmtree` still running (minutes on a 500 MB
tree). Six hours covers both by orders of magnitude; the youngest husk measured
was 28 hours old. It is declared in `args/worktree_husk_sweep.yaml`, overridable
with `KANBAN_WORKTREE_HUSK_AGE_HOURS`, and the kill switch is
`KANBAN_WORKTREE_HUSK_SWEEP=0`.

## An incident during this card's own test run, recorded because it is the subject matter

The first run of `tests/kanban/test_worktree_husk_sweep.py` (14:48 UTC) pointed
`_sweep_old_worktrees` at a temp `WORKTREE_BASE` but left
`tools.git.worktree_paths.worktree_root` and `_remove_worktree` unpatched, with
`max_age_days=0`. The 7-day sweeper walked the LIVE sanctioned root and removed
four worktrees its predicate proved clean and fully pushed:

| path | repo | branch | age at removal |
|---|---|---|---|
| cli/adhoc/ftl-conc-01 | icdev_ft | kanban/ftl-conc-01 @ 3469b15 | 0.1 d |
| cli/adhoc/ftl-nav-01 | icdev_ft | kanban/ftl-nav-01 @ bbc0809 | 0.0 d |
| cli/genesis-daemon-26916/mfx-sib-02-repair | ICDev | kanban/mfx-sib-02 @ 65a08e4c0 | 0.8 d |
| cli/kanban-scheduler-2980/fix-cd1d099fff | ICDev | fix/cd1d099fff-rebase @ 9da0d7b0f | 0.9 d |

The two icdev_ft worktrees went through the sweeper's ORPHAN branch: ICDev's git
said "is not a working tree", `_worktree_is_disposable` read them clean, and
`rmtree` ran — the predicate is repo-agnostic and the remover is not. All four
were re-created at the same path on the same branch at the same commit within
the hour (`git worktree prune` + `git worktree add` in the owning repo), and
each re-reads clean with nothing unpushed. Tracked content is identical to what
was removed; anything git-ignored inside them (a `.env`, a `.venv`) is not
recoverable and should be re-created by whoever owns the checkout. The test
file now pins every seam the 7-day sweeper reads (`_isolate_the_seven_day_sweep`)
and the husk module's own tests never reach the sweeper at all.
