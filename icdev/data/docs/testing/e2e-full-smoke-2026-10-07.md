# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-10-07 (task-e2e-0de5201d)

Route smoke gate and the full Playwright suite, run as the
`[AUTO-RUN] Playwright E2E Suite — full smoke` card prescribes.
Successor to [`e2e-full-smoke-2026-10-02.md`](e2e-full-smoke-2026-10-02.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all`) | **PASS — 88/88** (78 nav routes + 10 API endpoints), 0 failures, 38s |
| 2. Full E2E (`npx playwright test tests/e2e/ --project=chromium --reporter=line`) | **853 total: 838 passed, 0 failed, 15 skipped** — 38.2m, exit 0 |
| 3. `capture_playwright(...)` build log | **Captured** at 2026-10-07T12:49:32Z, `returncode=0`, `passed=838`, `failed=0`, `skipped=15`, `failures: []` |

No failing tests, so there are no failing titles to list. Counts match the
2026-09-23 AUTO-RUN (838/0/15); the 2026-10-02 run had one more skip (837/0/16).

## This was attempt #2 — and the order of steps 1 and 2

Attempt #1 was failed as `no_commits`. It had in fact run step 2 to completion in this
worktree, against this same tree (`c88b29ec3`, unchanged since): the line-reporter
output ends `15 skipped / 838 passed (38.2m) / EXIT=0`, written at 2026-10-07 12:16:52Z.
It then ended without running step 3 or committing a record.

This attempt did not re-run the 38-minute suite. It ran the route smoke gate (step 1,
88/88), fed attempt #1's output to `capture_playwright` (step 3), and wrote this record.
Step 1 therefore ran **after** step 2 rather than before it; since it passed, it would
not have stopped step 2, so the result stands — but the gate did not precede the run
it gates, and that is stated rather than hidden.

## How step 2 ran — not isolated

The diagnostics banner reports `base URL : http://localhost:5050 (server:
playwright-managed)`. Unlike the 2026-10-02 run, attempt #1 did not set
`ICDEV_E2E_BASE_URL` / `ICDEV_DASHBOARD_PORT` / `ICDEV_PG_DATABASE`, so the suite ran
on the canonical port and the database that server was configured with, not a
throwaway `icdev_e2e`. The fixtures it writes may be on the live board.

## Note on the card's step-3 return code

The card derives `rc` as `0 if 'failed' not in out.lower() else 1`. In this output
`failed` appears 0 times, so the heuristic and Playwright's real exit code (0) agree
this time. (On 2026-09-26 and 2026-10-02 it would have recorded a green run as failed
because of `[WebServer]` log lines.)

## Skips — 15

The line reporter does not name skipped tests, so the per-spec split is not recorded
here. A skipped test is not a passing test, and none of these 15 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Raw line-reporter output, route smoke output | `.tmp/pw_out.txt`, `.tmp/route_smoke.txt` in the task worktree (gitignored, disposable) |
| Build log event | `.logs/build.ndjson`, `playwright_run` at 2026-10-07T12:49:32Z |
| Screenshots | `.tmp/test_runs/screenshots/` (342 files) |
| HTML report | not produced — `--reporter=line` on the command line replaces the config's `json`/`html` reporters |

The run rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`
again. That is test-output churn, so it was restored and not committed.
