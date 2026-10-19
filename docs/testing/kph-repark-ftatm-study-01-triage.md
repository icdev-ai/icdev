# CUI // SP-CTI

# Triage — kph-repark-ftatm-study-01 (2026-10-19)

**Subject:** `ftatm-study-01` / `-02`, parked by `worktree-isolation-guard` at
2026-10-15T23:00:32Z, 23:22:27Z and 23:23:14Z ("worktree creation failed").
**Filed by:** `kanban_stranded_reflex` (REPARK card `kph-repark-ftatm-study-01`).
**Subtasks:** d1 (capture the error), d2 (repo root), d3 (this note), d4 (verify).

## Verdict: wrong repo root (class b), already fixed by #2503. No timeout change, no new code

Subtask d1 did not get the git error, because the guard's transition reason does
not include stderr, so d2 and d3 were skipped as "no class found". The error was
in `.logs/tools.genesis.reflexes.kanban.ndjson` all along. These are the lines the
guard says were "logged above", verbatim:

```
22:59:51Z git worktree add for ftatm-study-02 exceeded its 30s budget after 32.3s and was KILLED ...
          the partial worktree at C:\AI\ICDev\.tmp\worktrees\ftatm-study-02 was removed.
23:00:32Z git worktree add for ftatm-study-01 exceeded its 30s budget after 41.7s ... C:\AI\ICDev\.tmp\worktrees\ftatm-study-01
23:21:55Z git worktree add for ftatm-study-02 exceeded its 30s budget after 31.8s ...
23:22:27Z git worktree add for ftatm-study-01 exceeded its 30s budget after 32.2s ...
23:23:14Z git worktree add for ftatm-study-01 exceeded its 30s budget after 32.9s ...
```

What timed out was a **30s budget kill (class a)**, but on the wrong tree. Every
add targeted `C:\AI\ICDev\.tmp\worktrees\`, the ~21,000-file PUBLIC checkout. The
`ftatm-` stream had been seeded without registering it in
`args/kanban_external_repos.yaml`, so `resolve_task_repo` sent all 25 FT_ATM cards
to ICDev's root (class b). An ICDev add on this host takes 5–25s alone and over 30s
under contention (mfx-own-06/08). The real target, `C:\ai\icdev_ft`, is much
smaller.

The fix is `5173886e2`, merged as #2503 at 2026-10-16T03:22:40Z, about four hours
after the last park. It added `ftatm: icdev_ft` to the registry and pinned it in
`tests/kanban/test_repo_registry.py::test_every_ft_domain_stream_on_the_board_is_external`.
All three parks ran on pre-fix code. The first dispatch after the merge is the one
in the table below.

## Evidence that the cause is gone

| check | result |
|---|---|
| `resolve_task_repo('ftatm-study-01')` on origin/main | `icdev_ft`, root `C:/ai/icdev_ft`, `is_external=True` |
| next dispatch, same code path (`_create_worktree`), 2026-10-16T07:19:58Z | `Created worktree for ftatm-study-01 at ...\icdev-kanban\icdev_ft\ftatm-study-01 in 0.4s (budget 30s)` |
| same, 07:20:03Z | `Created worktree for ftatm-study-02 ... in 0.4s (budget 30s)` |
| outcome | `ftatm-study-01` done, icdev_ft PR #543 merged 2026-10-16T08:43Z |
| `python -m tools.kanban.worktree_pool --status --json` (2026-10-19) | enabled, depth 2/2, both entries `healthy: true` |

Both adds took 0.4s against a 30s budget. That is the acceptance this card asked
for ("a worktree add for that repo through the same code path finishes in under
30s"), measured on real dispatches instead of a hand-run probe.

## Not done, deliberately

* The 30s `WORKTREE_ADD_TIMEOUT_SECONDS` was not raised, and no retry was added to
  the guard. The budget did its job: it refused a checkout of the wrong repo.
* The warm pool was not extended to external repos. It serves the ICDev root only
  (`expect_manifest`), and an icdev_ft add takes 0.4s. Nothing in that path needs a pool.
* One related slow add remains, on ICDev rather than ftatm.
  `kph-repark-ftatm-study-01-d1` (a REPARK id, so it resolves to ICDev) was killed at
  32.2s on 2026-10-18T10:53Z and then created in 14.6s at 11:37Z. That is the ICDev
  add contention the pool (mfx-own-09) covers, and the pool is healthy now.

## Gap worth a card (not fixed here)

The guard's park reason says "see the git worktree add failure logged above", but
it does not include that line. So d1 could not find the error from the board, and
two subtasks were closed as not applicable while the cause sat in the reflex log.
Putting the kill line (duration and target path) into the transition reason would
have shown "wrong repo" in the first REPARK card.
