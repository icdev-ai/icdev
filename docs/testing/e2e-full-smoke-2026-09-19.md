# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-19 (task-e2e-af7f74b0)

Full-suite run against all menus, use cases, canvases, and chat right panels.
Successor to [`e2e-full-smoke-2026-09-14.md`](e2e-full-smoke-2026-09-14.md). This
is attempt #2 of the AUTO-RUN card — attempt #1 produced no committed output
(`no_commits`); this document is that output.

## Results

| Outcome | Count |
|---|---|
| Passed | 837 |
| Failed | **1** (transient — see below; passes on re-run) |
| Skipped | 15 |
| **Total** | **853** |

Wall clock **44.0m**. Route smoke gate (step 1): **PASS, 88/88** (79 nav routes +
9 API endpoints) before the suite.

## The one failure — transient, not a product defect

`cpmp_performance.spec.ts:203` › `gcpl-perf-15: GET /api/cpmp/contracts/<id>/health`
failed with `apiRequestContext.get: connect ECONNREFUSED ::1:5050` — the shared
dashboard refused the connection for that one request; no assertion ran. The
neighbouring tests in the same file (`gcpl-perf-14`, `-16`) passed either side of
it. Re-running the whole spec file immediately afterward: **21/21 passed (41s)**,
including `gcpl-perf-15`. Net of the blip the suite is 838 / 0 / 15, identical to
the 2026-09-12 and 2026-09-14 runs.

The structured build log (`.logs/build.ndjson`) therefore holds a `playwright_run`
event with `returncode 1, passed 837, failed 1` for the run as executed — the card's
`capture_playwright` snippet derives `rc` from the word "failed" in the transcript,
so it records the transient as a failure. It was left as recorded rather than edited.

## How it was run — and what it did NOT isolate

```bash
python tools/testing/route_smoke.py --all
npx playwright test tests/e2e/ --project=chromium --reporter=line
```

Run exactly as the card specifies, which means **no throwaway database and no
private port**. `globalSetup` reported:

```
✓ E2E database confirmed (server):     measured on 'icdev' (via ICDEV_DATABASE_URL)
✓ E2E database confirmed (subprocess): measured on 'icdev' (via ICDEV_DATABASE_URL)
```

i.e. the suite ran against the **live `icdev` database and the live `:5050`
dashboard**, and its fixture writes landed there (qa-fail-6a87916931be3793). The
2026-09-14 report isolated onto `icdev_e2e` / port 5099; this one did not. That is
also why the `ECONNREFUSED` is plausible: the live `:5050` server is shared with the
kanban scheduler, genesis daemon and other sessions, and can reload mid-run.
A future card run wanting isolation should follow the 2026-09-14 recipe.

## Skips — 15

Unchanged in shape from 2026-09-14 (`e2e_me_conflict_lifecycle` 5, `clawhub` 4,
`skillhub` 4, `dwo_restart_durability` 1, `dwo_trigger_linkage` 1): thirteen need a
service or fixture this deployment lacks; two are deliberate opt-ins. A skip is an
unmeasured test, not a passing one.

## Artifacts

| What | Where |
|---|---|
| Structured build log | `.logs/build.ndjson` (gitignored) |
| Console transcript | `.tmp/pw_out.txt` (gitignored) |

`--reporter=line` replaces the config's reporter list, so no JSON/HTML report was
produced (same note as the prior two reports). This document is the durable record.
Running the suite also rewrote the tracked
`playwright/screenshots/xrv-cost-04-spend-panel.png`; that is test output churn and
is deliberately not committed here.
