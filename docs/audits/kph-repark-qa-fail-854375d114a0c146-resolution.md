# kph-repark-qa-fail-854375d114a0c146 — resolution

**Card:** `[REPARK] qa-fail-854375d114a0c146: parked twice by worktree-isolation-guard within 24h`
**Filed by:** `kanban_stranded_reflex`, which correctly refused to requeue a recurring park a second time.
**Resolved:** 2026-10-19. All times UTC.

---

## Verdict

The cause was an EMPTY warm worktree pool (`tools/kanban/worktree_pool.py`,
mfx-own-09): with no pooled checkout to hand out, dispatch fell back to a cold
`git worktree add` while the host was under IO/memory pressure, the add
overran its 30s budget, and the guard parked the task rather than build in the
shared checkout. The pool was refilled by hand to depth 2 at ~11:1x on
2026-10-17, the subject was moved out of `validating` by hand at 11:22:39, and
every dispatch of it after that obtained a worktree. It is now `done`, merged
as PR #2517.

**No requeue was performed**, because requeueing a `done` task whose PR is on
`main` would dispatch a worker with nothing to build. Nothing here raised the
30s budget and nothing retries inside the guard.

## The subject's transitions, verbatim from `kanban_status_transitions`

| recorded_at | transition | actor | reason (abridged) |
|---|---|---|---|
| 10-16 23:49:32 | `scheduled -> validating` | worktree-isolation-guard | park 1: worktree creation failed |
| 10-17 00:18:16 | `validating -> scheduled` | kanban_requeue_reflex | orphan_validating, requeued once |
| 10-17 00:24:08 | `scheduled -> validating` | worktree-isolation-guard | park 2, 6 min later |
| 10-17 11:22:39 | `validating -> backlog` | cli | add exceeded 30s under host pressure; pool empty (2 husks); **pool refilled by hand to depth 2** |
| 10-17 12:49:42 | `scheduled -> token_exhausted` | scheduler | provider failover, held |
| 10-17 19:49:41 | `token_exhausted -> in_progress` | scheduler | re-dispatched (worktree obtained) |
| 10-17 19:52:47 | `in_progress -> done` | manual | forced: not reproducible, 6/6 GETs 200 |
| 10-17 19:53:19 | `done -> backlog` | scheduler | unverified: no commits on task branch |
| 10-17 21:05:06 | `scheduled -> in_progress` | scheduler | dispatched (worktree obtained) |
| 10-17 21:10:03 | `in_progress -> pr_opened` | scheduler | PR opened |
| 10-17 21:31:57 | `pr_opened -> done` | pr_watcher | PR #2517 merged |

Both post-refill dispatches got past the isolation guard. The subject landed
as commit `405039209` (`docs(testing): triage /boundary/compliance-debt
timeout -- transient, 2/2 pass on re-run`), merged at `3bcc38e40`.

## Re-derived on 2026-10-19

```
$ python tools/kanban/cli.py --show qa-fail-854375d114a0c146
  status:   done
  updated:  2026-10-17 21:31:57.926756+00:00

$ python -m tools.kanban.worktree_pool --status
worktree pool  ENABLED   depth 2 / target 2 (max 3)
  OK wt-d2d2a2bcc5c0  age 0.05h  healthy
  OK wt-bd2a56066a3d  age 2.17h  healthy
```

The pool is at target depth and refilling on its own (one entry is 3 minutes
old), so the condition that produced both parks is not present now.

## What this record does not claim

It does not explain why the pool was empty with two husks on 2026-10-16. The
husk sweep (`tools/kanban/worktree_husks.py`, mfx-own-04) runs on a clock of
hours, and the refill backs off ten minutes after a failed add (mfx-own-09), so
a run of failed refills under host pressure can leave the pool empty for a
while. If this card's pattern comes back, the next step is to read
`worktree_pool --status` at the moment of the park, not to raise the budget.
