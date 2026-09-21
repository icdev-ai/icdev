# CUI // SP-CTI

# QA-Agent Full E2E Suite Sweep — 2026-09-21 (task-qa-sweep-22b88872)

Route smoke gate followed by the full Playwright suite through `qa_agent_runner`.
Successor to [`e2e-full-smoke-2026-09-19.md`](e2e-full-smoke-2026-09-19.md). This is
attempt #2 of the AUTO-RUN card — attempt #1 produced no committed output
(`no_commits`); this document is that output.

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all --json`) | **PASS — 88/88**, 0 failures, no 500s, nothing excluded or tolerated |
| 2. Full E2E (`qa_agent_runner.py --run --json`) | **PASSED — 853 total: 837 passed, 0 failed, 16 skipped** |
| 3. File failure tasks | Not applicable — 0 failures, 0 unparsed failures, nothing filed |
| 4. Persist to `ace_qa_runs` | **Recorded** as `qa-1790001127` (`record_error: null`, `recorded_failures: 0`) |

69/69 spec files ran (`not_run 0`, `no_report 0`) in 12 batches; wall clock 19.8m
(14:32:07 → 14:51:56 UTC). 624 screenshots captured. Every batch reported `ok`.

## Host stalls — none

The `StallSampler` ran beside all 12 batches (218 samples at 5 s): `host_stalls 0`,
`health_slow 0`, `failures_during_stall 0`. 26 samples were `unreachable`, which is
expected — Playwright starts and stops its own webServer per batch, so a probe
between batches finds nothing listening (qa-fail-5cacee65f1d03c8c).

## How it was run — isolated

Unlike the 2026-09-19 run, this one did NOT write fixtures into the live board
(qa-fail-6a87916931be3793). A spare port had to be named as well as the database,
because a canonical dashboard already holds `:5050` and `reuseExistingServer` would
otherwise attach to it and leave every exported variable inert:

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5090
ICDEV_DASHBOARD_PORT=5090
ICDEV_PG_DATABASE=icdev_e2e
python tools/testing/qa_agent_runner.py --run --json --record --deadline-seconds 5400
```

Confirmed live, mid-run, against the runner's own server — not by re-reading the
environment: `curl http://127.0.0.1:5090/api/health` →
`{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}`.
Ports 5085–5099 were checked free beforehand and 5090 was released after the run.

## Skips — 16

| Spec | Count | Reason |
|---|---|---|
| `clawhub.spec.ts` | 4 | ClawHub service not running on port 5077 |
| `skillhub.spec.ts` | 4 | SkillHub service not running on port 5077 |
| `e2e_me_conflict_lifecycle.spec.ts` | 5 | seed session `sess-9cc6891cb548` fixture not present in this environment |
| `dwo_restart_durability.spec.ts` | 1 | opt-in (`ICDEV_E2E_DWO_RESTART=1`) |
| `dwo_trigger_linkage.spec.ts` | 1 | opt-in (`ICDEV_E2E_DWO_TRIGGER=1`) |
| `nav_regression_probes.spec.ts` | 1 | no seeded pulse post on this DB |

The 2026-09-19 run skipped 15. The difference is the last row: it needs a seeded
pulse post that exists on the canonical board and not on the throwaway `icdev_e2e`
database, so it is a cost of isolation, not a regression. Its script-inertness is
covered at source by pytest (nav-sec-07). A skip is an unmeasured test, not a
passing one — none of these 16 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Run row | `ace_qa_runs`, run id `qa-1790001127` |
| Route smoke JSON, run JSON, per-batch reports | `.tmp/` in the task worktree (gitignored, disposable) |

Running the suite also rewrote the tracked
`playwright/screenshots/xrv-cost-04-spend-panel.png`; that is test output churn and
was restored, not committed. This document is the durable record.
