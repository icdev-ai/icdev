# task-e2e-a104c341 — Playwright E2E full smoke (2026-10-13)

Base: `77feae85c` (main, Merge PR #2488). Host: win32 10.0.26300, node 24.11.0, python 3.14.0.

## Step 1 — route smoke

`python tools/testing/route_smoke.py --all` against `http://127.0.0.1:5050`

**PASS — 88/88 checks OK** (78 pages + 10 API endpoints). No broken routes, so the full suite was allowed to run.

## Step 2 — full Playwright suite

`npx playwright test tests/e2e/ --project=chromium --reporter=line`

| Result | Count |
|--------|-------|
| Passed | 838 |
| Failed | 0 |
| Skipped | 15 |
| **Total** | **853** |

Wall clock: 40.7 min, exit 0. Raw output: `.tmp/pw_out.txt`. The suite was run by attempt #1 of this
task (finished 18:49 EDT) against this same base. Attempt #2 reused that output and did not rerun it, and
it reran the route smoke above.

## Step 3 — build log

Recorded via `tools.logging.build_logger.capture_playwright(returncode=0, stdout=...)`.

## Failing tests

None.

## Artifacts

- Screenshots: `.tmp/test_runs/screenshots/` (341 files)
- HTML report: `npx playwright show-report`

## Why attempt #1 failed

The suite was green, but the attempt committed nothing, so verification classified it `no_commits`.
The only working-tree changes were the regenerated `.tmp/pw_out.txt` and a re-rendered
`playwright/screenshots/xrv-cost-04-spend-panel.png`. Both are test by-products and are not committed here.
This report is the run's committed record.
