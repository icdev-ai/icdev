# CUI // SP-CTI

# Taking `git worktree add` off the dispatch critical path (mfx-own-09)

Measured on `C:/AI/ICDev`, 2026-09-12, git 2.55.0.windows.5, tree at
`origin/main` = `7ac421e49` (20,352 tracked files, ~275 MB per checkout).

Re-derive everything here with:

```bash
python -m tools.kanban.worktree_pool --status --json
python -m tools.kanban.worktree_pool --measure --window-hours 24 --json
python -m tools.kanban.host_io --survey
```

---

## 1. What was already settled, and is not reopened

| lever | verdict | why |
|---|---|---|
| IO-throttle the CI containers | **unavailable on this host** | docker ACCEPTS `--device-write-bps /dev/sda:10mb` and does not ENFORCE it: 200 MB written at 608 MB/s unlimited and 720 MB/s "limited" (cgroup v2 + overlayfs under WSL2, where the container overlay does not map to that block device). |
| fewer CI runners | **would backfire** | the fleet's total IO work is fixed, so spreading it over fewer runners lengthens each run and WIDENS the window a dispatch can collide with. mfx-own-08 measured 37 of 91 adds overlapping a CI run. |
| "CI in flight -> decline to dispatch" | **already refused in writing** | `docs/audits/mfx-own-08-worktree-add-caller-vs-host-survey.md` §7: fires on 40.7% of adds to prevent 15, against the 1.63% CLAUDE.md calls grounds for standing a check down. |
| `host_io` as a dispatch GATE | **already refused** | it cannot predict the next add — 0% precision on the slow-add hypothesis, ~25% on the killed-add one at a 12.79% fire rate. That verdict stands. It is used here for a **different** question (when to refill a pool), where a wrong answer wastes a checkout instead of parking a task. |

The residual mfx-own-08 left is the one this card takes: **the add is on the
critical path of a dispatch**, so a slow host minute becomes a
`worktree-isolation-guard` park.

## 2. The cost being removed

From mfx-own-08, on this host:

| condition | add duration | killed at the 30s budget |
|---|---|---|
| CI in flight | 23.0 s median | 15 of 37 |
| no CI | 8.1 s median | 0 of 54 |

Fisher exact p = 1.7e-7. On the live board `kanban_status_transitions` holds
**96** `worktree-isolation-guard` parks (2026-08-25 → 2026-09-12), 9 of them in
the 48 hours before this card.

## 3. The claim path, component by component

A pool entry is a complete checkout on a throwaway branch `kanban-pool/<hex>` at
a path that names no task. A dispatch converts it. Measured on the quiet host,
same tree, same minute:

```
git worktree add -c checkout.workers=0   3.62 s     <- what a dispatch does today
------------------------------------------------
git branch -m kanban-pool/x kanban/<id>  0.14 s
git worktree move <pool> <task path>     0.10 s     <- a rename; the tree does not move
git reset --hard origin/main             0.64 s
verify (list + HEAD + status)            0.19 s
------------------------------------------------
CLAIM TOTAL                             ~1.07 s
```

**The claim does not scale with the tree.** A pool entry 12 hours stale (65
commits, 243 changed files on `origin/main`) resets forward in **0.57 s**,
because `reset --hard` writes the delta while `worktree add` writes all 20,352
files.

## 4. A/B through the real dispatcher, arm order rotated

`tools.genesis.reflexes.kanban._create_worktree` itself, six trials in the order
`warm, inline, inline, warm, warm, inline` (rotated, because the first checkout
in a process pays a cold cache and a fixed order credits that entirely to
whichever arm goes first):

| arm | n | min | median | max |
|---|---|---|---|---|
| warm (pool claim) | 3 | 1.18 s | **1.38 s** | 1.85 s |
| inline (`git worktree add`) | 3 | 3.94 s | **4.15 s** | 4.28 s |

Both arms produced a correct worktree: registered, `.git` present, HEAD on
`kanban/<id>`, clean status, at `origin/main`'s commit, `tools/manifest.md`
present.

**NOT MEASURED, and stated rather than implied:** the claim's duration *under CI
load*. No CI run was in flight during this session. The argument that it stays
short is structural, not empirical — a claim performs no checkout of the tree,
only a ref update, a directory rename and a delta reset (243 files worst case
against 20,352, an ~84x difference in bytes touched). Re-measure it the next
time `--measure` records a park while the pool was warm.

## 5. Pool vs just-in-time pre-create — the card asked for both

| | warm pool | JIT pre-create of `.tmp/worktrees/<id>` |
|---|---|---|
| claim cost | 1.38 s median (rename + move + delta reset) | **0.07 s** — one `git worktree list --porcelain`; the dispatcher's existing `worktree_path.exists()` branch does the rest |
| entry is | **fungible** — one warm checkout serves whichever card the cycle picks | **bound to one task id** |
| speculative cost | one spare checkout, whatever the board does | one checkout **per due candidate**, and `_get_due_tasks` routinely returns more candidates than a cycle dispatches |
| side effects on the board | none — the branch and path name no task | creates `kanban/<id>` and a 275 MB checkout for a card still in `scheduled`, which is exactly the shape `orphan_requeue` and the stranded audit read as a stuck build |

JIT wins on claim cost by 1.3 s and loses on everything that matters. Every gate
that decides whether a *due* task is actually dispatched — the per-task lease,
the landed check, sibling holds, the respawn guard — runs **after**
`_get_due_tasks`, so a JIT pre-create is a bet placed before the decision.

**The pool was chosen.** The 1.3 s difference is 0.3% of a dispatch that then
runs an autonomous worker session for minutes.

## 6. What the pool costs

- **Disk:** `target_size: 2` × 275 MB = ~550 MB steady state, ceiling
  `max_size: 3`. This is a disk budget, not a throughput one.
- **Per cycle when at target:** one `git worktree list --porcelain` (0.07 s) plus
  one `git status --porcelain` per entry (0.09 s) ≈ 0.25 s, on the way *out* of
  the cycle, after the dispatches.
- **Per refill:** one real `git worktree add` (7.7 s measured), under the same
  30 s budget and the same `checkout.workers=0` as a dispatch add, holding the
  same cross-process `worktree_add_lock` with a **5 s** wait — so it yields to a
  dispatch that is adding rather than queueing ahead of it.
- **After a FAILED refill: ten minutes of silence.** A refill add is killed at
  the same 30 s budget, and on an *idle* board `host_io` reports `UNMEASURABLE`
  — no dispatch add was recorded because no dispatch happened — so the quiet
  check correctly allows a refill. Without a cooldown the pool would burn 30 s of
  disk per cycle for a whole CI window, competing with the very runs slowing it
  down. That is a cost this card would otherwise have **added**, and the stamp is
  a file under the pool root because two dispatchers refill against one disk and
  a cooldown only one of them can see is not a cooldown. It is not a retry
  budget and not a backoff ladder: the next attempt is an ordinary attempt.

## 7. The honesty rails, and how each is proven

| rail | proof |
|---|---|
| an empty pool falls through to today's inline add | `test_empty_pool_declines_rather_than_failing`, and measured live: 3.90 s, correct worktree |
| a pool defect can never fail a dispatch | `test_claim_never_raises_even_when_git_is_broken` |
| a FAILED `git worktree list` is not an empty pool | `test_unreadable_listing_declines_and_does_not_invent_an_empty_pool` — the kpr-dup-10 inversion |
| a half-claim is torn down, never left | `test_a_failed_reset_tears_the_claim_down_...`, `test_a_failed_verification_tears_the_claim_down` |
| refill failures are named, never swallowed | `test_refill_failures_are_named_never_swallowed`, and `measure` reads them back from the log |
| the budget stays 30 s and no retry is added | `test_the_dispatchers_budget_is_not_raised_and_no_retry_is_added`, `test_the_pool_uses_the_dispatchers_add_budget_and_never_its_own` (reads the reflex's AST) |
| nothing on the creation path can park | `test_the_claim_is_tried_before_the_add_and_cannot_park` — asserted over CALLS, not text |
| a pool branch can never read as a task branch | `test_pool_branches_can_never_be_read_as_a_task_branch` |
| the pool does not accumulate | `test_reap_enforces_the_ceiling`, `test_reap_removes_an_aged_entry_...`, plus the 7-day `_sweep_roots` backstop |
| the pool never writes to the board | `test_the_pool_module_never_writes_to_the_board` |

## 8. Two defects found by running it

1. **`pool_root` resolved relative to the caller's `_repo_root`.** The dispatcher
   hands its own repo root around, which is a *linked worktree* whenever a cycle
   runs from one — so the first live claim read `<worktree>/.tmp/worktree-pool`,
   found nothing, and reported `pool_empty` for a pool that was full. `pool_root`
   now canonicalises whatever it is given. Pinned by
   `test_a_linked_worktree_reads_the_MAIN_checkouts_pool`.
2. **The pyc scrub ran on one of two creation paths.** `_create_worktree`'s
   tracked-`*.pyc` guard sat inline after the add, so a claimed worktree would
   have skipped it. Extracted to `_scrub_tracked_pycs` and called from both.

## 9. Where the pool lives, and why not under `.tmp/worktrees`

`.tmp/worktree-pool/wt-<hex>`, a **sibling** of `.tmp/worktrees`. Every direct
child of the latter is read by `worktree_husks` (mfx-own-04) as
`<base>/<task-id>`; a pool entry parked there would be refused as `no_board_row`
on every sweep and would pollute the one survey whose job is to name real husks.

The consequence is stated rather than papered over: **`sweep_husks` structurally
cannot act on a pool entry**, because its `board_row` precondition asks the board
for the task id a directory is named after and a pool entry is named after no
task. So the pool applies that module's *rule* itself — `newest_mtime` imported,
never re-derived, and `None` (cannot tell) never acts — and `_sweep_roots` adds
the 7-day path as a backstop, where a pool entry is safe by construction: its
branch is `kanban-pool/<hex>`, so `_worktree_task_id` returns `None`, the
`in_progress` guard cannot be fooled, and `_worktree_is_disposable` still has to
prove the checkout holds no work.

## 10. The acceptance measurement is a TOOL, and it has not yet had 24 hours

The card asks for ">=24h on this host: zero `worktree-isolation-guard` parks
while the pool is non-empty". That window had not elapsed when this landed, so
the number is not quoted here — `python -m tools.kanban.worktree_pool --measure
--window-hours 24` re-derives it from primary data at any time, and its three
buckets are never merged:

- `parks_while_pool_nonempty` — **the finding**, and the acceptance number;
- `parks_while_pool_empty` — the sanctioned fallback (the inline add ran and the
  host beat it);
- `parks_unmeasurable` — no pool observation before the park, so the pool may not
  have been running. **Not a clean result.**

Counts are `None`, never `0`, when the pool has recorded nothing in the window.

## 11. Named residuals

- **The claim's duration under CI load is unmeasured** (§4). Structural argument
  only.
- **The 7-day `_sweep_old_worktrees` backstop does not take the pool lock**, so
  it could in principle remove an entry in the moment between a claim reading it
  and moving it. The claim then fails its post-move verification, tears down and
  falls through to an inline add — the safe outcome. The window also requires an
  entry older than 7 days, which the pool's own 24 h `entry_max_age_hours`
  prevents; the backstop exists for entries the pool's reaper never reaches.
- **Two dispatchers can each decide to refill at the same instant**, creating one
  entry over `target_size`. Bounded by the number of concurrent refills,
  re-checked against `max_size` immediately before each add, and reaped.
- **`sweep_husks` cannot act on a pool entry** (§9). The pool applies that
  module's rule itself.
- **The pool's NDJSON is written by whichever checkout runs it** while `measure`
  reads the canonical one. They coincide for the scheduler, which runs in the
  main checkout; a pool run from a linked worktree reports `unmeasurable` there,
  never a false clean.
