# `git worktree add` is OFF the dispatch critical path -- a warm pool (mfx-own-09)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.worktree_pool --status [--json]      # depth, each entry's health, refill verdict
python -m tools.kanban.worktree_pool --refill               # top up to target; ONE add, under the SAME 30s budget
python -m tools.kanban.worktree_pool --reap                 # ceiling + age + a .git-less entry
python -m tools.kanban.worktree_pool --measure --window-hours 24 [--json]   # THE acceptance number
```

mfx-own-08 proved the CALLER innocent (a full dispatcher replica runs at 1.10x
a hand add over 44 interleaved trials) and the HOST guilty: 23.0s median and
15 of 37 adds KILLED with self-hosted icdev_ft / icdev_rt CI on this same
machine, against 8.1s and 0 of 54 without (Fisher exact p = 1.7e-7). Each kill
is a `worktree-isolation-guard` park -- 96 on this board's record -- and a
second park inside 24h files a repark card and demands a human. THE THREE
OBVIOUS LEVERS WERE MEASURED AND REFUSED, and are NOT reopened: docker ACCEPTS
`--device-write-bps` under WSL2 and does not ENFORCE it (720 MB/s "limited");
fewer runners spreads a FIXED amount of IO over a LONGER window; and a
CI-in-flight dispatch gate fires on 40.7% of adds to prevent 15, against the
1.63% CLAUDE.md calls grounds for standing a check down.
SO THE ADD MOVES, rather than getting faster or better-timed. A pool entry is a
complete checkout on a THROWAWAY branch `kanban-pool/<hex>` at a path naming NO
task; a dispatch CLAIMS one -- `git branch -m` 0.14s, `git worktree move` 0.10s
(a RENAME), `git reset --hard origin/main` 0.64s, verify 0.19s. THE CLAIM DOES
NOT SCALE WITH THE TREE: a 12h-stale entry (65 commits, 243 files) resets in
0.57s, because reset writes the DELTA where an add writes all 20,352 files.
MEASURED through `_create_worktree` ITSELF, arm order ROTATED over six trials
(the first checkout in a process pays a cold cache):
  warm   1.18 / 1.38 / 1.85 s      inline   3.94 / 4.15 / 4.28 s
NOT MEASURED, and named: the claim under CI LOAD -- none was in flight. The
argument is STRUCTURAL (no checkout happens) and is re-measured by `--measure`
the first time a park lands while the pool was warm.
A POOL, NOT A JIT PRE-CREATE, and both were measured. JIT has the CHEAPER claim
(0.07s -- the dispatcher's existing exists-branch) and was still rejected: a JIT
entry is BOUND TO ONE TASK ID while a pool entry is FUNGIBLE, and every gate
that decides whether a due task actually dispatches (lease, landed check,
sibling holds, respawn guard) runs AFTER `_get_due_tasks` -- so a JIT entry for
a task the cycle then skips leaves a `kanban/<id>` branch and 275 MB for a card
in `scheduled`, the exact shape orphan_requeue reads as a stuck build.
AN EMPTY POOL IS NOT AN ERROR. `claim` returns None for EVERY doubt -- empty,
lock busy, an unreadable `git worktree list` (a FAILED listing is never an
empty pool: that inversion is kpr-dup-10), a failed health check, a task branch
that already exists -- and never raises, so the caller falls through to today's
inline add, BYTE-IDENTICAL (measured: 3.90s, correct worktree).
WORKTREE_ADD_TIMEOUT_SECONDS STAYS 30, no retry is added, and the guard's park
for a genuinely failed add is untouched. The refill runs under the SAME budget
and the SAME `checkout.workers=0`, pinned to the reflex's literals by an AST
test -- more patience there would put a 60s checkout on the disk the dispatcher
needs, making the pool the noisy neighbour it exists to protect against.
A HALF-CLAIM IS TORN DOWN, NEVER LEFT. The dangerous state is a worktree at the
TASK's path on the WRONG branch, because the dispatcher's exists-branch would
adopt it and hand a worker somebody else's tree. So the BRANCH is renamed FIRST
(a ref update, reversible) and the directory moved SECOND, and any failure after
the move removes worktree AND branch so the inline add starts clean. Provably
safe: a pool entry has never held work, verified clean before anything is touched.
REFILL ONLY WHEN THE HOST IS QUIET, and `host_io`'s WAIT is the ONLY measured
signal -- UNMEASURABLE STILL REFILLS, on purpose: it means no add was recorded
in the window, which is the ordinary state of an IDLE board, and refusing there
empties the pool exactly when filling it is cheapest. host_io stays REFUSED as
a dispatch GATE (it cannot predict the next add); this is a different question
with a different cost of being wrong -- A BAD REFILL WASTES A CHECKOUT, A BAD
GATE PARKS A TASK. Which signal was seen is REPORTED as `quiet_basis`.
It runs from a `finally` AFTER the cycle's dispatches (the idle exits are where
filling is most valuable) and takes the SAME cross-process `worktree_add_lock`
with a SHORT 5s wait, so it yields to a dispatch that is adding.
THE POOL REAPS ITSELF: `max_size` 3 is a hard ceiling, `entry_max_age_hours` 24
retires an unclaimed 275 MB entry, and a `.git`-less entry goes by mfx-own-04's
OWN rule -- `newest_mtime` IMPORTED, and None (cannot tell) NEVER acts. Stated
rather than papered over: `sweep_husks` structurally CANNOT act here, because
its `board_row` precondition asks the board for the task id a directory is
named after and a pool entry is named after NO task. `_sweep_roots` adds the
7-day path as a backstop, safe by construction (branch `kanban-pool/<hex>` ->
`_worktree_task_id` None -> the in_progress guard cannot be fooled).
PER-REPOSITORY BY CONSTRUCTION -- `pool_root` canonicalises the CALLER's repo
root, so an EXTERNAL task (compass / idea_lab) asks its own repository and finds
a directory nothing refills. One stat, no lock, no git.
THE ACCEPTANCE NUMBER IS A TOOL, and its three buckets are never merged:
`parks_while_pool_nonempty` (THE FINDING), `parks_while_pool_empty` (the
sanctioned fallback) and `parks_unmeasurable` (no observation before the park --
NOT a clean result). All None, never 0, over a window the pool recorded nothing
in. Config args/worktree_pool.yaml; kill switch KANBAN_WORKTREE_POOL=0, never a
shell neutraliser. Survey: docs/audits/mfx-own-09-warm-worktree-pool-survey.md
