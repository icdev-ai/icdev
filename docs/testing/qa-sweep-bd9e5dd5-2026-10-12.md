# CUI // SP-CTI

# QA-agent full E2E sweep — 2026-10-12 (task-qa-sweep-bd9e5dd5)

Record of the `[QA-AGENT] Full E2E Suite Sweep` card.

## 1. Route smoke

`python tools/testing/route_smoke.py --all --json`: `passed=true`, **88**
routes checked, **0** failures, no 500s.

## 2. Full E2E run — `qa-1791848505`

`python tools/testing/qa_agent_runner.py --run --json --trigger kanban:task-qa-sweep-bd9e5dd5`

| Field | Value |
|---|---|
| `status` | `failed` |
| total / passed / failed / skipped | 853 / 836 / **2** / 15 |
| spec files run | 69 of 69 (0 not run, 0 without a report), 12 batches |
| screenshots | 624 |
| started → completed | 2026-10-12 23:41:45Z → 2026-10-13 00:23:37Z (41m52s) |
| stall sampler | 489 samples over 12/12 batches, 0 host stalls, 0 slow, 0 unreachable |

The 2026-10-11 sweep (`qa-1791760651`) returned 853 / 838 / 0 / 15. Both
failures in this run are 10s Playwright request timeouts. The stall sampler
did not overlap either test window, so the host was not stalled when they
failed.

| Test | Spec | Error | Probe after the run |
|---|---|---|---|
| Cache Savings — Spend panel > the Spend section renders on the existing page | `cache_savings_spend_panel.spec.ts` | `apiRequestContext.get` timeout 10000ms on `GET /cache-savings` | 200 in 4.97s cold, then 0.66s warm. Slow cold render, not a broken page |
| gcpl-cdrl-10: POST /api/cpmp/sam/sync-awards returns 200 (graceful w/o API key) | `cpmp_cdrl.spec.ts` | `apiRequestContext.post` timeout 10000ms on `POST /api/cpmp/sam/sync-awards` | The route calls `tools.govcon.sam_contract_sync.sync_awards` synchronously (`tools/dashboard/api/cpmp.py:1073`) and can exceed the 10s budget when SAM.gov is slow or reachable |

Screenshots are under `.tmp/test_runs/playwright-artifacts-qa-1791848505-b0/`
and `-b2/` in the task worktree (`test-failed-1.png`).

## 3. Failure filing

Filed with `qa_agent_runner.file_failure_tasks()` (task type `fix`, backlog).
Each card records the error message and the screenshot path:

- `qa-fail-4056903498e8d9de`
- `qa-fail-b535d046a9341241`

### `qa-fail-4056903498e8d9de` — already fixed on main; the sweep tested stale code

The fix is `ca5e698c3` (`qa-fail-cf5b5d4e84be3839`, PR #2480). It moved the
spend panel's git sweep onto a worker thread, and a request now waits at most
`REFRESH_WAIT_SECONDS` (3s) for it. PR #2480 merged at 23:35:45Z. This run
started at 23:41:45Z, but the dashboard serving it (PID 6452 on :5050) had been
running since 18:45Z. Merging to main does not reload a running dashboard, so
the sweep exercised the old synchronous sweep. Proof: that process's
`GET /api/cache-savings/spend` returns no `refreshing` key, and the fix adds
that key to every payload.

Measured against `401884261` with the Flask test client and the panel cache
cleared before each request:

| Case | `GET /cache-savings` | Panel |
|---|---|---|
| Cold app, real sweep | 200 in 5.90s (includes first-request app init) | all four KPI labels present |
| Warm app, cache cleared | 200 in 4.36s | all four KPI labels present |
| `collect` slowed to 29s (the cold time measured in `qa-fail-cf5b5d4e84be3839`) | 200 in 3.29s | `unmeasurable`, all four labels present, no `$0.00` inside `#spend-by-card` |

No code change. Restarting the :5050 dashboard picks up the fix.

## 4. Persistence

`qa_agent_runner.py --status qa-1791848505` returns the `ace_qa_runs` row:
trigger `kanban:task-qa-sweep-bd9e5dd5`, status `failed`, 853 total,
836 passed, 2 failed, 624 screenshots. `persistence.recorded_failures = 2`
(`ace_qa_failures`).
