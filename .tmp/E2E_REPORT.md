# E2E Smoke Suite — Final Report

- **Task:** task-e2e-6aaac720-d5
- **Generated:** 2026-10-12
- **Sources:** `.tmp/route_smoke_out.txt` (#2458), `.tmp/pw_out.txt` + `.tmp/pw_exitcode.txt` (#2466), `.tmp/pw_captured.json` (#2467)

## 1. Route smoke

| Check | Result |
|-------|--------|
| Nav routes | 78/78 OK |
| API endpoints | 10/10 OK |
| **Overall** | **PASS — 88/88, exit=0** |

No route smoke failures. Slowest routes: `/genesis` (5452ms), `/ai-roi` (4188ms), `/dashboard/executive-view` (3903ms).

## 2. Playwright E2E (chromium, 1 worker)

| Metric | Count |
|--------|-------|
| Total | 853 |
| Passed | 838 |
| Failed | 0 |
| Skipped | 15 |
| Exit code | 0 |
| Wall time | 38.2m |

### Failing tests (up to 20)

None. `pw_out.txt` contains no `failed`/`flaky`/`Error:` lines, and the build-log capture
(`pw_captured.json`) records `failed: 0, failures: []`.

## 3. Artifacts

| Artifact | Path | Status |
|----------|------|--------|
| Screenshots | `.tmp/test_runs/screenshots/` (main checkout `C:\AI\ICDev`) | 365 files present. The newest is dated 2026-10-11 23:59 local, which is EARLIER than this run (2026-10-12 ~06:13 local), so they come from a previous run. This run's working tree was removed when the d3 card closed. |
| HTML report | `.tmp/test_runs/playwright-report${RUN_TAG}/` (from `playwright.config.ts`) | **Not found for this run.** The newest report on disk is `playwright-report-qa-1791607565-b11` (2026-10-10), so it predates this run. |
| Raw output | `.tmp/pw_out.txt` | committed |
| Build log | `.logs/build.ndjson` (event `playwright_run`) | recorded per `pw_captured.json` |

## Verdict

**GREEN.** Route smoke passed 88/88 and Playwright passed 838 tests, failed 0 and skipped 15.
