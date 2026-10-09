# CUI // SP-CTI

# QA-Agent Full E2E Suite Sweep — 2026-09-28 (task-qa-sweep-13cb8f84)

Route smoke gate followed by the full Playwright suite through `qa_agent_runner`.
Successor to [`e2e-full-smoke-2026-09-27.md`](e2e-full-smoke-2026-09-27.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all --json`) | **PASS — 88/88**, 0 failures, no 500s |
| 2. Full E2E (`qa_agent_runner.py --run --json`) | **PASSED — 853 total: 838 passed, 0 failed, 15 skipped** |
| 3. File failure tasks | **None filed** — zero failures, zero flaky |
| 4. Persist to `ace_qa_runs` | **Recorded** as `qa-1790567909` (trigger `kanban:task-qa-sweep-13cb8f84`, status `passed`) |

69/69 spec files ran in 12 batches (b0–b11); every batch reported `unexpected 0`, `flaky 0`.
Wall clock 38.6m (03:58:29 → 04:37:08 UTC), against 23.4m the day before. 624 screenshots captured.
The two intermittent timeouts of 2026-09-27 (`qa-fail-85c0984e518bac09` NOCC sla read,
`qa-fail-280ed0bc2f813fa2` WEX Studio S1-02) did not recur. Both cards had already been fixed
and closed. One green sweep does not prove an intermittent failure is gone.

| Batch | Passed | Skipped | Duration |
|---|---|---|---|
| b0 | 133 | 0 | 264s |
| b1 | 43 | 4 | 119s |
| b2 | 86 | 0 | 205s |
| b3 | 31 | 1 | 127s |
| b4 | 27 | 6 | 91s |
| b5 | 67 | 0 | 254s |
| b6 | 61 | 0 | 177s |
| b7 | 92 | 0 | 257s |
| b8 | 143 | 0 | 354s |
| b9 | 38 | 0 | 136s |
| b10 | 76 | 4 | 155s |
| b11 | 41 | 0 | 159s |

## Skips — 15

The previous sweeps skipped 16 and passed 835–837. This run passed 838 and skipped 15, so one
test that used to skip now runs and passes. Skips by spec:

| Spec | Skipped |
|---|---|
| `e2e_me_conflict_lifecycle.spec.ts` | 5 |
| `clawhub.spec.ts` | 4 |
| `skillhub.spec.ts` | 4 |
| `dwo_restart_durability.spec.ts` | 1 |
| `dwo_trigger_linkage.spec.ts` | 1 |

A skipped test is not a passing test; none of these 15 asserted anything.

## How it was run — NOT confirmed isolated

The run was produced by the task's first attempt, which recorded it but ended without a
commit (`no_commits`). This retry did not repeat the 38-minute run. It re-ran the route
smoke, then read the results back from `ace_qa_runs` and the 12 saved
`playwright-results-qa-1790567909-b*.json` reports.

Unlike the 2026-09-23 → 09-27 sweeps, this run's own diagnostics
(`e2e-env-diagnostics-qa-1790567909-b0.json`) record `dashboardUrl: http://localhost:5050`
and `ICDEV_DATABASE_URL …/icdev`. They do not record `ICDEV_E2E_BASE_URL=…:5090` or
`ICDEV_PG_DATABASE=icdev_e2e`. So the recipe from qa-fail-6a87916931be3793, a spare port plus
a throwaway database, **cannot be shown to have been used**. The run may have attached to the
canonical `:5050` via `reuseExistingServer` and written E2E fixtures to the live `icdev`
database. The next sweep should name the port and the database explicitly:

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5090
ICDEV_DASHBOARD_PORT=5090
ICDEV_PG_DATABASE=icdev_e2e
python tools/testing/qa_agent_runner.py --run --json --record --deadline-seconds 5400
```

The first attempt also left four partial run ids before the recorded one (`qa-1790562994`,
`qa-1790566348`, `qa-1790567056`, `qa-1790567182`: 1–4 batches each, none in `ace_qa_runs`).
It also left one single-batch artifact directory after it, `qa-1790570381`. Why those were
abandoned was not recoverable from the artifacts, and nothing here relies on them.

## Artifacts

| What | Where |
|---|---|
| Run row | `ace_qa_runs`, run id `qa-1790567909` |
| Failure cards | none |
| Per-batch reports, env diagnostics, screenshots | `.tmp/test_runs/` in the task worktree (gitignored, disposable) |

The run again rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`.
That is test output churn, so it was restored and not committed.
