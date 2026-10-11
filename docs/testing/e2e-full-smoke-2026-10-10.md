# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-10-10 (task-e2e-39ea938d)

Route smoke gate and the full Playwright suite, run as the
`[AUTO-RUN] Playwright E2E Suite — full smoke` card prescribes.
Successor to [`e2e-full-smoke-2026-10-08.md`](e2e-full-smoke-2026-10-08.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all`) | **PASS — 88/88** (78 nav routes + 10 API endpoints), 0 failures |
| 2. Full E2E (`npx playwright test tests/e2e/ --project=chromium --reporter=line`), isolated | **853 total: 837 passed, 0 failed, 16 skipped**, 19.8m, exit 0 |
| 3. `capture_playwright(...)` build log | **Captured** at 2026-10-10T23:41:54Z, `returncode=0`, `passed=837`, `failed=0`, `skipped=16`, `failures: []` (see the note on the superseded 23:40:20Z entry below) |

No tests failed, so there are no failing titles to list. The counts match the
2026-10-08 run (837/0/16).

Tree under test: `eac9bc874` (origin/main at dispatch).

## This was attempt #2

Attempt #1 failed as `no_commits`. This attempt ran step 1 before step 2. Step 2 was
a fresh run.

## How step 2 ran: isolated, on the second try

The isolated run set `ICDEV_DASHBOARD_PORT=5093`, `ICDEV_E2E_BASE_URL=http://127.0.0.1:5093`
and `ICDEV_PG_DATABASE=icdev_e2e`. The diagnostics banner reports
`base URL : http://127.0.0.1:5093 (server: playwright-managed)`. globalSetup measured
both writers: `E2E database confirmed (server): measured on 'icdev_e2e'` and
`E2E database confirmed (subprocess): measured on 'icdev_e2e'`.

**The first launch was not isolated.** It ran the card's command exactly as written
(`npx playwright test tests/e2e/ ...`, no env overrides). With `.env`'s DSN in
effect, it attached to the live dashboard on `localhost:5050`. globalSetup reported
`measured on 'icdev'`, meaning the canonical board. The run was stopped at test 8 of 853 and
relaunched isolated. **The stop did not reach the node process.** Stopping the
background shell killed only the bash wrapper. The first run's Playwright process
kept running against `localhost:5050` / `icdev` until about test 745. Every new worker it spawned
then died on startup with `worker process exited unexpectedly
(code=3221225794)` (0xC0000142, STATUS_DLL_INIT_FAILED: its console was gone).
It never printed a summary, and it was no longer running when the isolated run finished.

Consequences:

- **Canonical board residue: none found in `kanban_tasks`.** Measured read-only at
  23:41Z. The newest row on `icdev` was created 22:06:44Z, before either run started.
  Other tables were not surveyed.
- **`.tmp/pw_out.txt` holds both runs interleaved.** The progress counter climbs to
  `[745/853]`, then restarts at `[25/853]`. The 59 numbered `worker process exited
  unexpectedly` blocks in that file all belong to the stray first run. The
  isolated run's own summary (`16 skipped / 837 passed (19.8m)`, exit 0) is the last
  block in the file, and it counts only that run's tests.
- **The first build-log capture (23:40:20Z) is wrong.** It was fed the interleaved
  file, so it recorded `passed=837, failed=0` with 59 bogus `failures` entries.
  `build.ndjson` is append-only. The corrected entry at 23:41:54Z was captured from
  the isolated run's summary block alone and records `failures: []`. Read the later entry.

Lesson for the card: the step-2 command as written targets the canonical database
whenever `.env` sets `ICDEV_DATABASE_URL`. It should carry the isolation env
(`ICDEV_DASHBOARD_PORT` + `ICDEV_E2E_BASE_URL` + `ICDEV_PG_DATABASE=icdev_e2e`) that
`playwright.config.ts` documents. The 2026-10-08 record ran that way too.

## Note on the card's step-3 return code

The card derives `rc` as `0 if 'failed' not in out.lower() else 1`. The capture used
Playwright's real exit code (0) and stripped `[WebServer]` lines, as on 2026-10-08.

## Skips — 16

The line reporter does not name skipped tests, so the per-spec split is not recorded
here. A skipped test is not a passing test.

## Artifacts

| What | Where |
|---|---|
| Raw line-reporter output | `.tmp/pw_out.txt` in the task worktree (gitignored, disposable; both runs interleaved, see above) |
| Build log event | `.logs/build.ndjson`, `playwright_run` at 2026-10-10T23:41:54Z (supersedes 23:40:20Z) |
| Screenshots | `.tmp/test_runs/screenshots/` (342 files) |
| HTML report | not produced: `--reporter=line` on the command line replaces the config's `json`/`html` reporters |

The run rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`
again. That file is test-output churn, so it was restored and not committed.
