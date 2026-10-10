# CUI // SP-CTI

# QA-Agent Full E2E Suite Sweep — 2026-09-29 (task-qa-sweep-835620c9)

Route smoke gate followed by the full Playwright suite through `qa_agent_runner`.
Successor to [`e2e-full-smoke-2026-09-28.md`](e2e-full-smoke-2026-09-28.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all --json`) | **PASS — 88/88**, 0 failures, no 500s |
| 2. Full E2E (`qa_agent_runner.py --run --json`) | **PASSED — 853 total: 837 passed, 0 failed, 16 skipped** |
| 3. File failure tasks | **None filed** — zero failures, zero flaky |
| 4. Persist to `ace_qa_runs` | **Recorded** as `qa-1790652629` (trigger `kanban:task-qa-sweep-835620c9`, status `passed`) |

69/69 spec files ran in 12 batches (b0–b11); every batch reported `unexpected 0`, `flaky 0`.
Wall clock 17.0m (03:30:29 → 03:47:30 UTC), against 38.6m the sweep before. 624 screenshots captured.
Stall sampler: 186 samples across all 12 batches, 0 host stalls, 0 `health_slow`. The 26
`unreachable` samples are gaps between batches, when one batch's server has stopped and the
next has not yet started. No test failed, so none of them overlaps a failure.

| Batch | Passed | Skipped | Duration |
|---|---|---|---|
| b0 | 133 | 0 | 107s |
| b1 | 43 | 4 | 63s |
| b2 | 86 | 0 | 95s |
| b3 | 31 | 1 | 56s |
| b4 | 27 | 6 | 47s |
| b5 | 67 | 0 | 83s |
| b6 | 61 | 0 | 86s |
| b7 | 92 | 0 | 111s |
| b8 | 142 | 1 | 136s |
| b9 | 38 | 0 | 68s |
| b10 | 76 | 4 | 53s |
| b11 | 41 | 0 | 91s |

## Isolated this time: port 5093, database `icdev_e2e`

The 2026-09-28 sweep could not show that it was isolated. This one can:

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5093 ICDEV_DASHBOARD_PORT=5093 ICDEV_PG_DATABASE=icdev_e2e \
  python tools/testing/qa_agent_runner.py --run --json --record \
  --trigger kanban:task-qa-sweep-835620c9 --deadline-seconds 5400
```

- `e2e-env-diagnostics-qa-1790652629-b0.json` records `dashboardUrl: http://127.0.0.1:5093`
  and `ICDEV_PG_DATABASE=icdev_e2e`.
- During b0 the Playwright-managed server on `:5093` answered `/api/health` with
  `database: icdev_e2e, database_measured: true`. That was measured against the running server,
  not read from the environment. The diagnostics file still shows the ambient
  `ICDEV_DATABASE_URL …/icdev`. `webServerDatabaseEnv()` rewrites that DSN for the server
  Playwright starts.
- Before the run, nothing was listening on 5090–5099. The only QA runner process during the
  run was this one.

We used `:5093` rather than the documented `:5090` because two sessions can bind the same spare
port on Windows. A less common port lowers the chance of colliding with another sweep.

## Skips — 16

| Spec | Skipped |
|---|---|
| `e2e_me_conflict_lifecycle.spec.ts` | 5 |
| `clawhub.spec.ts` | 4 |
| `skillhub.spec.ts` | 4 |
| `dwo_restart_durability.spec.ts` | 1 |
| `dwo_trigger_linkage.spec.ts` | 1 |
| `nav_regression_probes.spec.ts` | 1 **(new vs 2026-09-28)** |

The new skip is `a pulse post renders without executing injected script`. Its reason reads:
*"No seeded pulse post on this DB; script-inertness of pulse_post.html is covered at source by
pytest (nav-sec-07)."* **Running isolated caused this skip.** The throwaway `icdev_e2e`
database holds no pulse post, and the live `icdev` database that the previous sweep probably
ran against does. So 838 → 837 passed is not a regression. It is one browser-level assertion
that only ran while the sweep was writing to the live board. If that assertion should hold
under isolation too, the spec has to seed its own pulse post. That is a follow-up, and this
sweep did not make it.

A skipped test is not a passing test; none of these 16 asserted anything.

## Earlier attempts

This worktree also holds artifacts from three earlier run ids: `qa-1790649403` (1 file),
`qa-1790650492` (26), and `qa-1790652483` (b0 only). None is in `ace_qa_runs`, and none of their
processes was alive when this run started. Nothing here relies on them.

## Artifacts

| What | Where |
|---|---|
| Run row | `ace_qa_runs`, run id `qa-1790652629` |
| Failure cards | none |
| Per-batch reports, env diagnostics, screenshots | `.tmp/test_runs/` in the task worktree (gitignored, disposable) |

The run again rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`.
That is test output churn, so it was restored and not committed.
