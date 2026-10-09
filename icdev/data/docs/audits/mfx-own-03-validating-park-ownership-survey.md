# CUI // SP-CTI

# mfx-own-03 — who owns a card parked in `validating`?

**Measured 2026-09-05 against the live ICDEV[IT] PostgreSQL board.**
Every number below is re-derivable with the commands given beside it.

---

## 1. The question the card asks

A dispatch guard parks a task in `validating` when it refuses to build. The
guard is correct — failing closed beats building in the shared checkout — but
`validating` is terminal to every retry path: `stranded_audit` calls it
terminal, `promote_backlog_to_scheduled` dispatches `backlog`/`scheduled` only.
The card asks for an **owner** for that state.

The answer found by the survey is that an owner was **already built** —
`tools/kanban/orphan_requeue.py` (kpr-stale-05/06, on main since
2026-09-04 00:28 UTC) — and that it has **never once run**. The residual defect
is not a missing proof. It is reachability.

---

## 2. Guard parks and how each one ended (lifetime)

```
python -c "..."   # SELECT over kanban_status_transitions WHERE to_status='validating',
                  # then the first transition after each park with to_status <> 'validating'
```

| quantity | value |
|---|---|
| lifetime `-> validating` transitions | **102** |
| by actor | `worktree-isolation-guard` 59, `manual` 29, `repo-aware-guard` 14 |
| guard parks (the population in scope) | **73**, across **72** distinct tasks |

First exit after a guard park:

| exit | count | share |
|---|---|---|
| `manual -> done` | 39 | 53.4% |
| `cli -> scheduled` | 22 | 30.1% |
| `pr_watcher -> done` | 5 | 6.8% |
| *(none — still parked)* | 5 | 6.8% |
| `manual -> backlog` | 1 | 1.4% |
| `manual -> in_progress` | 1 | 1.4% |
| **`kanban_stranded_reflex` (the owner)** | **0** | **0.0%** |

**63 of 73 (86.3%) were rescued by a human** (`manual` 41 + `cli` 22). That is
the manual burden this card removes, and it is the denominator for the retry
budget.

---

## 3. Why the owner never ran

The act was wired as a downstream consumer of `stranded_audit`, inside
`kanban_stranded_reflex`. That audit walks **every terminal task**.

```
SELECT status, COUNT(*) FROM kanban_tasks WHERE status IN ('done','validating')
```

| population | rows |
|---|---|
| `done` | **3,892** |
| `validating` (the act's entire population) | **2** |

Recorded runs of the reflex (`genesis_audit WHERE reflex_name='kanban_stranded_reflex'`):

| quantity | value |
|---|---|
| recorded rows | **242** |
| rows carrying `orphan_requeue` in `details` | **0** |
| `watchdog_timeout_1200s` failures | **24** |
| success / failure / unknown | 21 / 114 / 107 |
| duration (n=45) | min 86.2s, **median 300.0s**, **max 1200.2s** against a 1200s watchdog |

Runs **since the act landed** (2026-09-04 00:28 UTC): **5**, of which **0**
carried the act; the one that reached a verdict died on
`watchdog_timeout_1200s`.

`genesis_reflex_state` at survey time:

```
consecutive_failures = 3
circuit_breaker_open = 1
circuit_breaker_tripped_at = 2026-09-04T11:58:56Z
last_error = watchdog_timeout_1200s
total_runs = 45, total_successes = 21, total_failures = 24
```

`tools/daemon/base.py` **skips** a reflex whose breaker is open. So the act was
unreachable twice over: it sat behind a ~20-minute whole-board audit that
exceeds its own watchdog, and once that tripped the breaker the reflex stopped
being dispatched at all.

---

## 4. The recurrence the card was written for

Both incidents are in the transition log, and the second is **after** the act
landed:

| when (UTC) | task | actor |
|---|---|---|
| 2026-09-04 13:34:38 | `mfx-sib-02` | `worktree-isolation-guard` |
| 2026-09-04 13:34:39 | `mfx-sib-01` | `worktree-isolation-guard` |
| 2026-09-04 13:35:17 | `mfx-mrg-02` | `worktree-isolation-guard` |
| 2026-09-04 13:41:27 | `fni-ana-01` | `repo-aware-guard` |
| 2026-09-05 02:06:00 | `flx-compose-01` | `worktree-isolation-guard` |
| 2026-09-05 02:06:13 | `flx-studio-01` | `worktree-isolation-guard` |
| 2026-09-05 03:17:02 | `fni-ana-01` | `repo-aware-guard` |
| 2026-09-05 04:35:13 | `flx-compose-02` | `worktree-isolation-guard` |

Five of the eight had been moved on by a human or another actor by survey time.
Two were **still parked**: `flx-compose-02` and `fni-ana-01`. `fni-ana-01` was
parked twice by the same guard inside 24h — the **recurring** shape, which the
existing act correctly cards rather than requeuing a third time.

---

## 5. The retry budget

The card proposes `failure_count` as the budget. The existing module already
bounds recurrence on **primary data** instead: a second park by the *same guard*
inside `REPARK_WINDOW_HOURS` (24h) is not requeued again — it gets one
`suggested` card carrying **both** parking reasons verbatim. That is strictly
better evidence than `failure_count`, which a requeue deliberately preserves but
which no guard park increments. **No second budget was added**; adding one would
be a second opinion on a question the transition log already answers.

Surveyed against this rule: of the 73 guard parks, `fni-ana-01` is the only
task in the recent window parked twice by one guard inside 24h — so the card
rule fires on 1 of 8 recent parks (12.5%) and the requeue on the rest.

---

## 6. What was built, and what deliberately was not

**Built:** `tools/genesis/reflexes/kanban_requeue_reflex.py` — a consumer that
reads candidates straight off the board (one indexed
`WHERE status = 'validating'` query, 2 rows today) and runs the *same two
proofs* from `tools/kanban/orphan_requeue.py`. 30-minute cadence, its own
circuit breaker, its own 300s budget. Plus `orphan_requeue.board_findings()`,
the one derivation of that candidate set, which `plan()` now also uses.

**Not built, on purpose:**

* the guard's 30s worktree timeout is **not raised** and the guard does **not**
  retry — both are forbidden by the guard's own comment, and a longer timeout
  only widens the contention window;
* `kanban_stranded_reflex` is **untouched** — it keeps its audit, its 24h
  cadence, its 1200s budget and its own breaker. An audit that legitimately
  times out *should* trip its own breaker; the point of this change is that
  doing so no longer takes the requeue down with it;
* no second proof, no second budget, no new table.

**Attribution, fixed on the way:** both acts stamped every requeue transition
and every `worktree_cleaned` intent row with `kanban_stranded_reflex`. That
reflex no longer runs the act and its breaker is open, so a reader following the
row would land on an impossible state. Both acts now take an `actor` (defaulting
to the old constant, so nothing else changes) and `kanban_requeue_reflex` passes
its own name.

---

## 6a. First live run (2026-09-05 05:23 UTC)

Dispatched once through the daemon, which is also what keeps
`capability_liveness` green for a newly declared reflex.

| task | verdict |
|---|---|
| `flx-compose-02` | **acted** — branch `kanban/flx-compose-02` proven empty (`git cherry` empty, ancestor of `origin/main`), worktree registered and `git status --porcelain` empty; worktree removed through `git worktree remove`, the LOCAL branch deleted, row requeued `validating -> scheduled` with `last_failure_reason` and `branch_name` cleared and `scheduled_at` set. |
| `fni-ana-01` | **carded** — parked twice by `repo-aware-guard` inside 24h (2026-09-04 13:41, 2026-09-05 03:17). One `suggested` card `kph-repark-fni-ana-01` carrying both parking reasons verbatim. Never requeued a third time. |

`genesis_reflex_state`: `total_runs 1, total_successes 1, last_metric_value 1.0,
circuit_breaker_open 0`.

Both verdicts were independently re-derived by hand against the repository
before the dispatch (`git cherry`, `git merge-base --is-ancestor`,
`git worktree list --porcelain`, `git status --porcelain`) — the act is not
trusted on its own report.

**This is the first automated exit from a guard park in the board's history:
0 of 73 before, 1 of 74 after.**

---

## 7. Not fixed here, and named

`kanban_stranded_reflex`'s breaker is open and
its audit still exceeds its watchdog on this board. That is a real finding about
the audit's cost, not about the requeue, and it needs its own card.

---

## 8. Unmeasurable, never a clean zero

A board that cannot be read reports `state: unmeasurable` with
`candidates: None` on both acts — never an empty list — while `success` stays
`True`. Marking an unreadable board a reflex *failure* is exactly what opened
the breaker that made the act unreachable; repeating it here would rebuild the
defect inside its own fix.
