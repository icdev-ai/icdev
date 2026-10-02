# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-10-02 (task-e2e-e07e7597)

Route smoke gate followed by the full Playwright suite, run as the
`[AUTO-RUN] Playwright E2E Suite — full smoke` card prescribes, and isolated on its
own port and database.
Successor to [`e2e-full-smoke-2026-09-29.md`](e2e-full-smoke-2026-09-29.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all`) | **PASS — 88/88** (78 nav routes + 10 API endpoints), 0 failures, 36s |
| 2. Full E2E (`npx playwright test tests/e2e/ --project=chromium --reporter=line`) | **853 total: 837 passed, 0 failed, 16 skipped** — 15.2m, exit 0 |
| 3. `capture_playwright(...)` build log | **Captured**, `returncode=0`, `failures: []` |

No failing tests, so there are no failing titles to list. Counts match the
2026-09-26 AUTO-RUN and the 2026-09-29 QA-agent sweep exactly.

## How it was run — isolated

```bash
ICDEV_E2E_BASE_URL=http://127.0.0.1:5094
ICDEV_DASHBOARD_PORT=5094
ICDEV_PG_DATABASE=icdev_e2e
npx playwright test tests/e2e/ --project=chromium --reporter=line
```

Measured against the Playwright-managed server while the run was in flight:
`GET http://127.0.0.1:5094/api/health` →
`{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}`.
The diagnostics banner reports `base URL : http://127.0.0.1:5094 (server: playwright-managed)`.
Wall clock was 11:31:40 → 11:46:51 UTC. Route smoke ran against the canonical `:5050`
(read-only GETs), as the card's command does.

## This was attempt #2 — what attempt #1 left behind

Attempt #1 was failed as `no_commits`. It had started the suite in the background at
11:26:36 UTC on `:5090` and then ended its session without waiting for it. That run was
still alive when this attempt started, in this same worktree, and it was not a usable
result:

- Test 11 (`ai_ify.spec.ts:58`) hit `browserContext.newPage: Test timeout of 60000ms exceeded`.
- From test 12 on, the first test of every remaining spec file failed with
  `worker process exited unexpectedly (code=3221225794, signal=null)` — 65 times, 66
  failures in all. The run reached `[853/853]` about two minutes after it started and
  then sat without printing a summary.

`3221225794` is `0xC0000142`, `STATUS_DLL_INIT_FAILED`: the worker process could not be
started at all. The host had 14 GB of physical memory free at the time, and the same
suite on the same host passed 837/837 minutes later, so this is read as a consequence
of the launching session having ended under the run, not as a product or host-capacity
failure. That cause is inferred from the timing; it was not reproduced.

The orphaned tree (root pid 31600, seven processes including its dashboard on `:5090`)
was stopped by pid before this run started, so the two could not share the screenshot
and artifact directories. Nothing else was killed. Its raw output is kept beside this
run's as `.tmp/pw_out_attempt1.txt`.

The practical point for this card: step 2 takes about 15 minutes, and a session that
backgrounds it and stops produces both no commit and a run that destroys itself. This
attempt held the session open until Playwright exited.

## Note on the card's step-3 return code

The card derives `rc` as `0 if 'failed' not in out.lower() else 1`. In this output
`failed` appears 9 times, all in `[WebServer]` log lines and none in a test result —
the same 9 the 2026-09-26 record found. That heuristic would have recorded a failure
for a green run, so the build log got Playwright's real exit code (0) and the counts
from its summary lines.

## Skips — 16

Same total as 2026-09-26 and 2026-09-29. The line reporter does not name skipped
tests, so the per-spec split is not recorded here; the 2026-09-29 record has it. A
skipped test is not a passing test, and none of these 16 asserted anything.

## Artifacts

| What | Where |
|---|---|
| Raw line-reporter output, route smoke output | `.tmp/pw_out.txt`, `.tmp/route_smoke.txt` in the task worktree (gitignored, disposable) |
| Build log event | `.logs/build.ndjson`, `playwright_run` at 2026-10-02T11:47:03Z |
| Screenshots | `.tmp/test_runs/screenshots/` (338 files) |
| HTML report | not produced — `--reporter=line` on the command line replaces the config's `json`/`html` reporters |

The run rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`
again. That is test-output churn, so it was restored and not committed.
