# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-23 (task-e2e-2170cb2b)

Route smoke gate followed by the full Playwright suite, run exactly as the
`[AUTO-RUN] Playwright E2E Suite — full smoke` card prescribes.
Successor to [`e2e-full-smoke-2026-09-22.md`](e2e-full-smoke-2026-09-22.md).

## Results

| Step | Outcome |
|---|---|
| 1. Route smoke (`route_smoke.py --all`) | **PASS — 88/88** (78 nav routes + 10 API endpoints), 0 failures |
| 2. Full E2E (`npx playwright test tests/e2e/ --project=chromium --reporter=line`) | **853 total: 838 passed, 0 failed, 15 skipped** — 41.0m, 1 worker, exit 0 |
| 3. `capture_playwright(...)` build log | **Captured**, `returncode=0` |

No failing tests, so there are no failing titles to list.

## Differences from 2026-09-22

- **Skips 16 → 15, passes 837 → 838.** The line reporter does not name skipped
  tests, so which one moved is not recorded here. The 2026-09-22 list (ClawHub /
  SkillHub on :5077, the `sess-9cc6891cb548` seed, two opt-in DWO specs, and the
  pulse-post probe) is the likely set. The pulse-post probe skipped on 2026-09-22
  only because the isolated `icdev_e2e` DB had no seeded post. This run used the
  live DB, which does have one, so that probe is the most likely one that now ran.
- **Not isolated.** The card's step 2 command names no throwaway database or port.
  So Playwright managed its own server on `localhost:5050`, and the env diagnostics
  measured it on **`icdev`** (via `ICDEV_DATABASE_URL`) for both the server and
  subprocesses. The banner warned: *"Nothing requested a throwaway database — the
  suite writes its fixtures into 'icdev'."* E2E fixture rows from this run are
  therefore on the live board (see qa-fail-6a87916931be3793). The 2026-09-22 sweep
  avoided this with `ICDEV_E2E_BASE_URL=http://127.0.0.1:5090`,
  `ICDEV_DASHBOARD_PORT=5090` and `ICDEV_PG_DATABASE=icdev_e2e`. The AUTO-RUN card's
  command should adopt the same settings.

## Artifacts

| What | Where |
|---|---|
| Raw line-reporter output, route smoke output | `.tmp/pw_out.txt`, `.tmp/route_smoke.txt` in the task worktree (gitignored, disposable) |
| Env diagnostics snapshot | `.tmp/test_runs/e2e-env-diagnostics.json` |
| Screenshots | `.tmp/test_runs/screenshots/` (341 files) |
| HTML report | `npx playwright show-report` |

The run rewrote the tracked `playwright/screenshots/xrv-cost-04-spend-panel.png`
again. That is test-output churn, so it was restored and not committed.
