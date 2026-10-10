# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-10-08 (task-e2e-413289bf)

Route smoke gate and the full Playwright suite, run as the
`[AUTO-RUN] Playwright E2E Suite — full smoke` card prescribes.
Successor to [`e2e-full-smoke-2026-10-07.md`](e2e-full-smoke-2026-10-07.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all`) | **PASS — 88/88** (78 nav routes + 10 API endpoints), 0 failures |
| 2. Full E2E (`npx playwright test tests/e2e/ --project=chromium --reporter=line`) | **853 total: 837 passed, 0 failed, 16 skipped**, 19.3m, exit 0 |
| 3. `capture_playwright(...)` build log | **Captured** at 2026-10-08T23:47:51Z, `returncode=0`, `passed=837`, `failed=0`, `skipped=16`, `failures: []` |

No tests failed, so there are no failing titles to list. The counts match the
2026-10-02 run (837/0/16). The 2026-10-07 run had one fewer skip (838/0/15).

Tree under test: `6d5858c7f` (origin/main at dispatch).

## This was attempt #2

Attempt #1 failed as `no_commits`. This attempt ran the steps in the card's order:
step 1 before step 2. Step 2 was a fresh run, not a reuse of attempt #1's output.

## How step 2 ran: isolated

The run set `ICDEV_DASHBOARD_PORT=5093`, `ICDEV_E2E_BASE_URL=http://127.0.0.1:5093` and
`ICDEV_PG_DATABASE=icdev_e2e`. The diagnostics banner reports
`base URL : http://127.0.0.1:5093 (server: playwright-managed)`. globalSetup
measured both writers on the throwaway database:
`E2E database confirmed (server): measured on 'icdev_e2e'` and
`E2E database confirmed (subprocess): measured on 'icdev_e2e'`. Fixtures did not
land on the canonical board.

## Note on the card's step-3 return code

The card derives `rc` as `0 if 'failed' not in out.lower() else 1`. In this run's raw
output the word `failed` appears 9 times, all in `[WebServer]` log lines, so the
heuristic would have recorded a green run as `rc=1`. The capture used Playwright's
real exit code (0) instead, and was fed the output with `[WebServer]` lines stripped.

## Skips — 16

The line reporter does not name skipped tests, so the per-spec split is not recorded
here. A skipped test is not a passing test.

## Artifacts

| What | Where |
|---|---|
| Raw line-reporter output | `.tmp/pw_out.txt` in the task worktree (gitignored, disposable) |
| Build log event | `.logs/build.ndjson`, `playwright_run` at 2026-10-08T23:47:51Z |
| Screenshots | `.tmp/test_runs/screenshots/` (338 files) |
| HTML report | not produced — `--reporter=line` on the command line replaces the config's `json`/`html` reporters |

The run rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`
again. That file is test-output churn, so it was restored and not committed.
