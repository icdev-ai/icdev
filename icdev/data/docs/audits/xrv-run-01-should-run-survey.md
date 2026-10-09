<!-- CUI // SP-CTI -->
# xrv-run-01 — should_run: the budget rung, replayed over every recorded dispatch

- **Card:** xrv-run-01 — "should_run: ONE pre-dispatch verdict, with the budget
  rung nothing consults today (loopx shape)"
- **Measured:** 2026-09-11, against the live PG board (`icdev`, measured via
  `tools.db.storage.active_database()`), from the `xrv-run-01` worktree with
  `--env-file C:/AI/ICDev/.env`
- **Re-derive:** `python -m tools.kanban.should_run --survey [--window-days 30] [--json]`
- **Report only.** `KANBAN_SHOULD_RUN=report` is the default and this card does
  NOT arm `enforce`. Exit 0 = a report was produced whatever it says; exit 2 =
  it could not be, which is never the same as a clean survey.

## The defect

`_dispatch_to_claude` ran six sequential early-return guards (manual build,
admission, external-repo park, recent success, open PR, circuit breaker), each
with its own log line and no shared record, and `_get_due_tasks` ran five more
before it. None of the eleven was a budget. The four USD guards the platform
declares — `token_tracker.check_budget`, `module_budget_tracker.check_module_budget`,
`cost_budget.apply_to_chain`, the proxy budgets — all sit inside
`tools/llm/router.py::invoke`. A `claude_cli` dispatch never enters the router,
so every cap was bypassed at dispatch and the budget refused the run's calls
MID-RUN instead. kpr-dup-09 (done<->backlog 95 flips in 5.5 hours) was this
shape: a run that ran out of budget, every transition legitimate.

## What shipped

`tools/kanban/should_run.py` — one `assess(task)` that CALLS every predicate the
dispatcher already consults plus the budget rung, folds them through one pure
`classify`, and re-implements none (AST-pinned: no SQL naming the board table,
the admission rule imported, the three budget seams called on the live path).
Wired as ONE call at the top of `_dispatch_to_claude`; one log line per dispatch:

    kanban: should_run <verdict> for <id>: <reasons>

`report` (default) changes no outcome — asserted by a test that drives the real
dispatch function with a `wait` verdict and sees the executor spawned exactly as
before. `enforce` parks a `wait`/`refuse` through `_move_task(..., "validating",
actor="should-run")`, exactly as admission does. `off` disables it.

The circuit breaker had two inline copies of `failure_count >= max_retries`
(the dispatch guard and the reaper's pre-park); both now read
`kanban._circuit_breaker_tripped(task)`, which `should_run` reads too.

## Method

The population is the `-> in_progress` transition written by the scheduler
(`kanban_status_transitions WHERE to_status='in_progress' AND actor='scheduler'`)
— the exact instant `assess` now runs, so it is the only population whose fire
rate describes it (the dispatch_admission idiom).

**Only the budget rung is replayed.** A lease, a pause sentinel or an open PR at
a past instant is recorded nowhere, so the other nine checks are `unmeasurable`
in replay and the distribution below is the budget rung folded through the SAME
`classify` the live path uses. `budget_check` is the shared fold: the live path
feeds it the seams' own dicts, the survey feeds it ledger readings.

The ledgers AS THEY WERE: for each dispatch at instant T,

| arm | cap and thresholds | spend as of T |
|---|---|---|
| module | `module_budget_periods` row for (`generative_intelligence`, month(T)) | `SUM(amount)`, `SUM(tokens)` over `module_budget_usage` from month start to T |
| token | today's `token_budgets` config for `kanban-scheduler` | `SUM(cost_estimate_usd)` over `agent_token_usage` from month start to T |
| cost | today's `cost_budget` config (limit 200.00, monthly, soft 0.5/0.8/0.95) | `SUM(cost_usd)` over `ai_telemetry` from period start to T |

`check_module_budget` and `check_budget` read "now" and take no as-of parameter,
so the survey's cap comparison is re-derived over the ledger rows and every row
it produces is labelled `basis: replayed_ledger`. The threshold semantics are the
trackers' own (a cap of 0 is unlimited; `block` only under `hard_stop`; `warn` at
`warning_threshold`). The soft-threshold ASK is a side effect and is not replayed;
a crossed threshold reads as `ask`.

## Result

Lifetime, 7,072 scheduler dispatches (2026-06-11 → 2026-09-11), 0 unparseable:

| verdict | dispatches | share |
|---|---|---|
| proceed | 5,084 | 71.89% |
| **wait** | **1,988** | **28.11%** |
| ask | 0 | 0.00% |
| refuse | 0 | 0.00% |
| unmeasurable | 0 | 0.00% |

Per arm: module `wait` 1,988 / `proceed` 5,084; cost `proceed` 7,072; token
`proceed` 7,072. **Every fire is the module arm's TOKEN cap.** Not one is USD:
`module_budget_periods.spent_usd` is 0.0 in every month, `ai_telemetry` carries
13,995 rows summing to $0.00, and `agent_token_usage` holds ONE row for
`kanban-scheduler` ($14.11) — the per-task cost writer is xrv-cost-02's, which
is why the token and cost arms cannot fire here yet.

By month:

| month | dispatches | proceed | wait | wait % | first wait | last wait |
|---|---|---|---|---|---|---|
| 2026-06 | 3,319 | 3,319 | 0 | 0.00 | — | — |
| 2026-07 | 762 | 762 | 0 | 0.00 | — | — |
| 2026-08 | 2,711 | 1,003 | 1,708 | 63.00 | 2026-08-07T23:13Z | 2026-08-30T02:35Z |
| 2026-09 | 280 | 0 | 280 | **100.00** | 2026-09-02T20:54Z | 2026-09-11T23:40Z |

The period rows: budget 150.00 USD / 400,000 tokens every month, `hard_stop` 1;
2026-08 closed at 420,513 tokens, 2026-09 reads 400,013 at measurement.

30-day window: 1,621 dispatches, 1,621 `wait`, fire rate 100.00% of measured.

Live, at measurement: `python -m tools.kanban.should_run --task xrv-run-02` reads
`WAIT` with `resets_at=2026-10-01T00:00:00+00:00` and nine other checks
`proceed`.

**Corroboration from the writer side.** `icdev.budget.module_budget.ndjson`
(the current file) carries 567 `budget exceeded` refusals between
2026-09-11T19:29Z and 23:44Z — four hours — for `timeout_extraction` (218),
`code_analysis` (168), `convergence_nlp_extract` (165), `anomaly_detection`,
`conformance_review`, `oracle_triage_llm`, `code_generation` (2) and
`gap_subject_extraction`; the rotated `_size` file holds 31,608 more. Those are
the mid-run refusals the card names: the dispatch went ahead, and every router
call inside the run was refused.

## What the number means, and why `enforce` stays off

28.11% lifetime and 100% this month is not a threshold to tune. It says two
things at once, and they point at different repairs:

1. **The gate was missing.** Since 2026-08-07 the scheduler has dispatched
   1,988 tasks into a ledger the platform's own budget layer would have refused,
   and nothing recorded that at dispatch. `should_run` now records it on every
   dispatch, in the scheduler log, with `resets_at`.
2. **The ledger it would enforce is a cost proxy, not a cost.** The tracker's
   own note (`module_budget_tracker.py`, measured 2026-08-28) says so: the token
   cap fired at 407,934 of 400,000 while the USD cap beside it sat at $0.00,
   because every call routes to a $0/1k provider. Arming `enforce` today would
   park EVERY task on the board until 2026-10-01 — correct against the ledger,
   and a stall against the estate.

So the default is `report`, and arming it needs the token cap moved after
routing or windowed to guard runaways (the tracker names that as its own card),
after which this survey is re-run. Do NOT raise `monthly_tokens` to quieten
the rung: the 1.63% this repo calls refusing routine work is a rate for a rule
that is right, and this rule is right about the ledger and the ledger is wrong
about the cost.

## Cost

Lifetime survey: 31.1s wall clock for 7,072 dispatches (three ledger reads per
dispatch, ~1.5ms each on the live PG). The live `assess` adds one
`dispatch_admission.assess` (a `gh pr list --head`) per dispatch beside the one
the admission guard below it already makes; the lease, board and ledger reads
are local.

## Not done, and named

- Capacity and sibling overlap are decisions over the CANDIDATE SET, not
  verdicts about one task, and stay in `_get_due_tasks`; the external-repo park
  is a fact about where a task builds. None is folded.
- The token and cost arms read today's config, not the config at the dispatch
  instant; a cap edited since would move replayed verdicts for old rows.
- Nothing here changes a cap, a threshold or a window.
