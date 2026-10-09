# CUI // SP-CTI

# QA-Agent Full E2E Suite Sweep — 2026-09-27 (task-qa-sweep-ed596a24)

Route smoke gate followed by the full Playwright suite through `qa_agent_runner`.
Successor to [`e2e-full-smoke-2026-09-26.md`](e2e-full-smoke-2026-09-26.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all --json`) | **PASS — 88/88**, 0 failures, no 500s |
| 2. Full E2E (`qa_agent_runner.py --run --json`) | **FAILED — 853 total: 835 passed, 2 failed, 16 skipped** |
| 3. File failure tasks | **Filed 2** via `file_failure_tasks()`: `qa-fail-85c0984e518bac09`, `qa-fail-280ed0bc2f813fa2` (both `fix`, `backlog`) |
| 4. Persist to `ace_qa_runs` | **Recorded** as `qa-1790479571` (`record_error: null`, `recorded_failures: 2`) |

69/69 spec files ran (`not_run 0`, `no_report 0`) in 12 batches, every batch `ok`;
wall clock 23.4m (03:26:11 → 03:49:34 UTC). 623 screenshots captured.
This breaks a run of four sweeps at 837/0/16 (2026-09-21 → 2026-09-26).

## Failures — 2, both intermittent timeouts

| Card | Test | Error | Screenshot |
|---|---|---|---|
| `qa-fail-85c0984e518bac09` | `noc_canvas.spec.ts` — NOCC — Read APIs > GET /api/noc/sla returns JSON | `page.textContent: Timeout 10000ms exceeded` waiting for `body` (27.5 s) | none captured |
| `qa-fail-280ed0bc2f813fa2` | `wex_e2e_lifecycle.spec.ts` — S1-02 AI Chat panel opens and accepts prompt | `locator.click: Timeout 10000ms exceeded` — the click completed, then hung in "waiting for scheduled navigations to finish" | `.tmp/test_runs/playwright-artifacts-qa-1790479571-b11/…/test-failed-1.png` (worktree, gitignored) |

Neither is a host stall: the `StallSampler` ran beside all 12 batches (257 samples at 5 s,
`host_stalls 0`, `health_slow 0`, `failures_during_stall 0`), and both failures carry
`during_stall: false`. 33 `unreachable` samples fall between batches, expected because
Playwright starts and stops its own webServer per batch (qa-fail-5cacee65f1d03c8c).

Re-run alone, same isolated server, `--no-record`:

| Spec | Runs | Result |
|---|---|---|
| `noc_canvas.spec.ts` | 1 | 15/15 passed |
| `wex_e2e_lifecycle.spec.ts` | 4 | 1 red (13/14 — a **different** test, S2-02, `page.screenshot: Timeout 10000ms`), then 3 × 14/14 |

So both reproduce intermittently, not deterministically. The WEX spec failed 2 of 5
executions, on two different tests with the same shape — a Studio page that stops
answering the driver (a click waiting on navigations, a screenshot that never completes)
— which points at the page rather than at either assertion. The NOCC failure is a
single observation. Both cards were filed because the recorded run failed; whoever takes
them should reproduce with repeated runs before changing anything, and should not read
one green run as a fix.

## How it was run — isolated

Same recipe as 2026-09-23 onward. A spare port was named so `reuseExistingServer`
could not attach to the canonical `:5050`, and fixtures went to a throwaway database
(qa-fail-6a87916931be3793):

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5090
ICDEV_DASHBOARD_PORT=5090
ICDEV_PG_DATABASE=icdev_e2e
python tools/testing/qa_agent_runner.py --run --json --record --deadline-seconds 5400
```

Confirmed live against the runner's own server: `curl http://127.0.0.1:5090/api/health` →
`{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}`.
Ports 5090–5092 were checked free beforehand.

The `ace_qa_runs` row and the two `fix` cards are on the live `icdev` board, not on
`icdev_e2e` — `current_database()` from a bare `get_connection()` returned `icdev` even
with `ICDEV_PG_DATABASE=icdev_e2e` exported. That is where they belong; it is noted so
nobody goes looking for them in the throwaway database.

## Skips — 16

Same total as the previous sweeps. The per-spec split was not re-derived for this run.
A skipped test is not a passing test; none of these 16 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Run row | `ace_qa_runs`, run id `qa-1790479571` |
| Failure cards | `qa-fail-85c0984e518bac09`, `qa-fail-280ed0bc2f813fa2` |
| Route smoke JSON, run JSON, re-run JSONs, per-batch reports | `.tmp/` in the task worktree (gitignored, disposable) |

The run again rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`.
That is test output churn, so it was restored and not committed.
