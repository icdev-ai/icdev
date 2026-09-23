# CUI // SP-CTI

# QA-Agent Full E2E Suite Sweep — 2026-09-22 (task-qa-sweep-3b8b4945)

Route smoke gate followed by the full Playwright suite through `qa_agent_runner`.
Successor to [`e2e-full-smoke-2026-09-21.md`](e2e-full-smoke-2026-09-21.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all --json`) | **PASS — 88/88**, 0 failures, no 500s |
| 2. Full E2E (`qa_agent_runner.py --run --json`) | **PASSED — 853 total: 837 passed, 0 failed, 16 skipped** |
| 3. File failure tasks | Not applicable — 0 failures, 0 unparsed failures, nothing filed |
| 4. Persist to `ace_qa_runs` | **Recorded** as `qa-1790125556` (`record_error: null`, `recorded_failures: 0`) |

69/69 spec files ran (`not_run 0`, `no_report 0`) in 12 batches, every batch `ok`;
wall clock 22.7m (01:05:56 → 01:28:40 UTC, 2026-09-23). 624 screenshots captured.
Counts are identical to the 2026-09-21 run.

## Host stalls — none

The `StallSampler` ran beside all 12 batches (248 samples at 5 s): `host_stalls 0`,
`failures_during_stall 0`. One `health_slow` sample (2.17 s, batch 4, sleep overshoot
7 ms, so not a host stall) coincided with no failure. 38 samples were `unreachable`,
expected between batches because Playwright starts and stops its own webServer per
batch (qa-fail-5cacee65f1d03c8c).

## How it was run — isolated

Same recipe as 2026-09-21. Fixtures went to a throwaway database, not the live board
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

## Skips — 16 (unchanged from 2026-09-21)

| Spec | Count | Reason |
|---|---|---|
| `clawhub.spec.ts` | 4 | ClawHub service not running on port 5077 |
| `skillhub.spec.ts` | 4 | SkillHub service not running on port 5077 |
| `e2e_me_conflict_lifecycle.spec.ts` | 5 | seed session `sess-9cc6891cb548` fixture not present in this environment |
| `dwo_restart_durability.spec.ts` | 1 | opt-in (`ICDEV_E2E_DWO_RESTART=1`) |
| `dwo_trigger_linkage.spec.ts` | 1 | opt-in (`ICDEV_E2E_DWO_TRIGGER=1`) |
| `nav_regression_probes.spec.ts` | 1 | no seeded pulse post on the isolated `icdev_e2e` DB |

A skipped test is not a passing test. None of these 16 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Run row | `ace_qa_runs`, run id `qa-1790125556` |
| Route smoke JSON, run JSON, per-batch reports | `.tmp/` in the task worktree (gitignored, disposable) |

The run again rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`.
That is test output churn, so it was restored and not committed.
