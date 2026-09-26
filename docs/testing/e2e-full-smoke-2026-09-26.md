# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-26 (task-e2e-22afec2a)

Route smoke gate followed by the full Playwright suite, run as the
`[AUTO-RUN] Playwright E2E Suite — full smoke` card prescribes — but **isolated**,
as the 2026-09-23 record recommended.
Successor to [`e2e-full-smoke-2026-09-24.md`](e2e-full-smoke-2026-09-24.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all`) | **PASS — 88/88** (78 nav routes + 10 API endpoints), 0 failures |
| 2. Full E2E (`npx playwright test tests/e2e/ --project=chromium --reporter=line`) | **853 total: 837 passed, 0 failed, 16 skipped** — 15.6m, exit 0 |
| 3. `capture_playwright(...)` build log | **Captured**, `returncode=0` |

No failing tests, so there are no failing titles to list. Counts match the
2026-09-24 QA-agent sweep exactly.

## How it was run — isolated

The card's step-2 command names no database or port, so the 2026-09-23 AUTO-RUN
wrote its fixtures into the live `icdev` board (qa-fail-6a87916931be3793). This run
set the same three variables the QA-agent sweeps use. With them,
`reuseExistingServer` cannot attach to the canonical `:5050`:

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5090
ICDEV_DASHBOARD_PORT=5090
ICDEV_PG_DATABASE=icdev_e2e
npx playwright test tests/e2e/ --project=chromium --reporter=line
```

Ports 5090–5092 were checked free beforehand. Confirmed live against the
Playwright-managed server: `GET http://127.0.0.1:5090/api/health` →
`{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}`.
Wall clock was 12:16:16 → 12:31:55 UTC, 15.6m. That is much faster than the 41.0m
of the non-isolated 2026-09-23 run. The cause was not investigated.

## Note on the card's step-3 return code

The card derives `rc` as `0 if 'failed' not in out.lower() else 1`. In this output,
`failed` appears 9 times, and all 9 are in `[WebServer]` request-log lines, none in
a test result. That heuristic would have recorded a failure for a green run, so the
build log got Playwright's real exit code (0) instead.

## Skips — 16

This is the same total as the 2026-09-24 sweep. The line reporter does not name
skipped tests, so the per-spec split is not recorded. A skipped test is not a
passing test, and none of these 16 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Raw line-reporter output, route smoke output | `.tmp/pw_out.txt`, `.tmp/route_smoke.txt` in the task worktree (gitignored, disposable) |
| Screenshots | `.tmp/test_runs/screenshots/` (338 files) |
| HTML report | `npx playwright show-report` |

The run rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`
again. That is test-output churn, so it was restored and not committed.
