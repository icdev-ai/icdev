# CUI // SP-CTI

# QA-Agent Full E2E Suite Sweep — 2026-09-24 (task-qa-sweep-d10f1037)

Route smoke gate followed by the full Playwright suite through `qa_agent_runner`.
Successor to [`e2e-full-smoke-2026-09-23.md`](e2e-full-smoke-2026-09-23.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all --json`) | **PASS — 88/88**, 0 failures, no 500s |
| 2. Full E2E (`qa_agent_runner.py --run --json`) | **PASSED — 853 total: 837 passed, 0 failed, 16 skipped** |
| 3. File failure tasks | Not applicable — 0 failures, 0 unparsed failures, nothing filed |
| 4. Persist to `ace_qa_runs` | **Recorded** as `qa-1790301271` (`record_error: null`, `recorded_failures: 0`) |

69/69 spec files ran (`not_run 0`, `no_report 0`) in 12 batches, every batch `ok`;
wall clock 20.4m (01:54:31 → 02:14:58 UTC, 2026-09-25). 624 screenshots captured.
Counts are identical to the 2026-09-21, 2026-09-22 and 2026-09-23 runs.

## Host stalls — none

The `StallSampler` ran beside all 12 batches (226 samples at 5 s): `host_stalls 0`,
`health_slow 0`, `failures_during_stall 0`. 27 samples were `unreachable`, expected
between batches because Playwright starts and stops its own webServer per batch
(qa-fail-5cacee65f1d03c8c).

## How it was run — isolated

Same recipe as 2026-09-23. Fixtures went to a throwaway database, not the live board
(qa-fail-6a87916931be3793), and a spare port was named so `reuseExistingServer` could
not attach to the canonical `:5050`:

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5090
ICDEV_DASHBOARD_PORT=5090
ICDEV_PG_DATABASE=icdev_e2e
python tools/testing/qa_agent_runner.py --run --json --record --deadline-seconds 5400
```

Confirmed live mid-run against the runner's own server: `curl http://127.0.0.1:5090/api/health` →
`{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}`.
Ports 5090–5092 were checked free beforehand.

## Skips — 16

Same total as the previous sweeps. The per-spec split was not re-derived for this run.
A skipped test is not a passing test; none of these 16 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Run row | `ace_qa_runs`, run id `qa-1790301271` |
| Route smoke JSON, run JSON, per-batch reports | `.tmp/` in the task worktree (gitignored, disposable) |

The run again rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`.
That is test output churn, so it was restored and not committed.
