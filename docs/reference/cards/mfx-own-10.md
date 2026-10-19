# The warm pool is WARM when a loaded host needs it (mfx-own-10)

```bash
python -m tools.kanban.worktree_pool --measure --window-hours 24 [--json]   # now reads the ROTATED logs too
python -m tools.kanban.worktree_pool --status                                # shows refill_in_flight
python -m tools.kanban.worktree_pool --refill --base origin/main            # what the detached child runs
```

mfx-own-09 moved `git worktree add` off the dispatch path. On 2026-10-18 the pool
was EMPTY when it mattered: 11 of 12 parks landed with depth 0, refills died at
the 30s budget 30 times in 50, and a burst (the repark wave) drained target=2 in
seconds while the quiet gate refused to refill. The pool worked on a quiet host
and failed on a loaded one, and the loaded host is the only case it was built for.

WHAT THE LOG SAID (every pool log on this host, 2026-09-12..10-19):
- 2026-10-14..18: 34 refills completed, 88 KILLED at 30s. The completed ones'
  p90 was 23.5s and max 28.6s, so the budget truncated the distribution. A
  killed checkout does not spare the disk. It throws away what it wrote, spends
  more IO deleting it, and then buys a 10-minute cooldown with the pool empty.
- The same window had 51 `host_busy` skips beside 99 `pool_empty` misses.
- Bursts: of 422 claims + misses, 111 started a run of 3+ asks within five
  minutes and 56 a run of 4+. The deepest was 7.
- `--measure` itself had gone blind. The log rotates daily, and `measure` read
  only the live file, so a 24h window asked at 08:46 saw one hour of events and
  every earlier park came out `unmeasurable`.

WHAT CHANGED:
- THE REFILL HAS ITS OWN BUDGET, `refill_add_timeout_seconds: 90`. The DISPATCH
  add keeps 30s, stays pinned to the reflex, and gets no retry. 90 is the
  dispatcher's add-LOCK wait, and the test pins the refill budget at or under
  it. A refill holds that lock while it checks out, so a dispatch queued behind
  one always gets the lock instead of timing out and overlapping it.
- THE REFILL IS DETACHED. `_tend_worktree_pool` calls `spawn_refill`, which
  checks the depth and probes a single-flight lock beside the pool. It spawns
  no process when there is nothing to do. Inline, a 90s refill would hold the
  cycle, and with it the next dispatch.
- A RUN FILLS TO TARGET, taking the add lock PER ADD and releasing it between
  entries. Target is 3 and max is 4, sized from the burst survey above.
- AN EMPTY POOL OVERRIDES WAIT. With nothing warm, the next dispatch runs its
  inline add on the same slow host anyway, so declining protects nobody. With
  an entry in hand the gate still holds.
- THE DISPATCHER ASKS TWICE. After `_create_worktree` gets the add lock (which
  a refill holds), it asks the pool again before its one add. That is a second
  claim, not a retry: no add has run, and a miss still falls through to the same
  single add. The second ask is logged as `phase=after_add_lock` and counted
  apart, so a pool-empty dispatch never reads as two misses.
- `convert_failed` NAMES ITS STEP and the last line git printed, e.g.
  `reset:fatal: ...`.
- `measure` reads the rotated siblings whose mtime falls inside the window. It
  also reports `refill_attempts` and `refill_budget_exceeded_share`, which is
  the acceptance ratio: budget kills over ATTEMPTS, not over failures.
- Logged depth is RE-READ after each add, not computed as `healthy + created`.
  Claims do not take the add lock, so the computed figure over-counted, and an
  over-count files a cold park as `pool_nonempty`.

NOT MEASURED, and named: the 24h-with-CI acceptance. CI was idle when this
landed, so no claim is made about the loaded-host numbers. Re-run
`--measure --window-hours 24` after a CI-active day. Success means
`parks_while_pool_empty` near 0 and `refill_budget_exceeded_share` well under 0.5.

Tests: `tests/kanban/test_worktree_pool_refill_under_load.py` (gated in
`args/ci_test_files/core.d/mfx-own-10.txt`).
