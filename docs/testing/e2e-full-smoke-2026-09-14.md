# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-14 (task-e2e-bfef4fba)

Full-suite run against all menus, use cases, canvases, and chat right panels.
Successor to [`e2e-full-smoke-2026-09-12.md`](e2e-full-smoke-2026-09-12.md). This
is attempt #2 of the AUTO-RUN card — attempt #1 produced no committed output
(`no_commits`); this document and the build-log capture are that output.

## Results

| Outcome | Count |
|---|---|
| Passed | 838 |
| Failed | **0** |
| Skipped | 15 |
| Did not run | 0 |
| **Total** | **853** (69 spec files executed, out of 83 entries in `tests/e2e/`) |

Wall clock **18.9m**, exit code **0**. Route smoke gate (step 1): **PASS, 88/88**
— 79 nav routes + 9 API endpoints, no failures, run against the shared `:5050`
dashboard before the suite. Re-run after the suite (against the same `:5050`
dashboard, once Playwright's own isolated server had shut down): **88/88 again**.

Identical pass/fail/skip shape to the 2026-09-12 run (838 / 0 / 15 of 853) — no
new product defect and no new harness defect since that report.

## How it was run

```bash
python tools/testing/route_smoke.py --all          # gate, 88/88

ICDEV_DASHBOARD_PORT=5099 \
ICDEV_E2E_BASE_URL=http://127.0.0.1:5099 \
ICDEV_PG_DATABASE=icdev_e2e \
  node node_modules/@playwright/test/cli.js test tests/e2e/ --project=chromium --reporter=line

python tools/testing/route_smoke.py --all          # post-suite, 88/88
```

* **The card's step 2 specifies `--reporter=line`.** As the 2026-09-12 report
  notes, a `--reporter` flag REPLACES the config's `[list, json, html]`
  reporter list, so no `.tmp/test_runs/playwright-results.json` and no HTML
  report were produced this run — only the console transcript
  (`.tmp/pw_out.txt`), which is where every count above was parsed from
  (`838 passed`/`15 skipped`, zero `✘`/`×` failure markers, zero `N failed`
  summary line). A future run wanting the JSON/HTML artifacts should omit
  `--reporter` per the prior report's recommendation; this run followed the
  card's literal instruction instead.
* **`icdev_e2e` is the throwaway database**, already bootstrapped (1,841
  tables via `tools/db/bootstrap_pg.py --check`). Confirmed by
  `globalSetup`'s dual measurement, not merely requested:
  ```
  ✓ E2E database confirmed (server):     measured on 'icdev_e2e' (via ICDEV_PG_DATABASE)
  ✓ E2E database confirmed (subprocess): measured on 'icdev_e2e' (via ICDEV_PG_DATABASE)
  ```
  Manually starting `tools/dashboard/app.py` with `ICDEV_PG_DATABASE=icdev_e2e`
  does **not** achieve this by itself — `.env`'s `ICDEV_DATABASE_URL` outranks
  the discrete `ICDEV_PG_DATABASE` name in `tools/db/storage.py`
  (qa-fail-6a87916931be3793), and a manual server started that way measured
  `icdev` on `/api/health`. Letting Playwright's own `webServer` start the
  process (`webServerDatabaseEnv` in `tests/e2e/fixtures/e2e_database.ts`)
  is what actually rewrites the DSN; a manually-started server was stopped
  before the real run.
* **Port 5099** — free at the time (5050, 5094, 5096, 5200 were the only other
  listeners on this host), chosen over the previous two reports' 5090/5091/5093
  to avoid any peer session reaching for the same value. Not independently
  re-verified as a single listener post-hoc (the previous two reports' stronger
  check); the server did shut down cleanly when Playwright exited (nothing
  listening on 5099 afterward).
* **The worktree carried no `.env` and no `node_modules`** — `.env` was copied
  from the main checkout, `node_modules` was a directory junction onto it
  (`mklink /J`), same as the prior two reports.
* **No second kanban scheduler was spawned.** A background loop touched
  `.tmp/kanban_scheduler.heartbeat` every 30s for the duration; the manual
  dashboard start (before it was stopped) logged
  `Kanban scheduler already running (fresh heartbeat …) — not spawning another`.
* **Coordination note honoured**: another session (`cli-claim-task-qa-sweep-b6ef196a`)
  was running its own QA E2E sweep concurrently against the same host. This run
  used its own port (5099) and Playwright's own isolated `webServer`/database, so
  the two should not have contended on a listener; no interference was observed
  in this run's output.

## Skips — 15, unchanged from 2026-09-12

| Count | Spec | Reason (the spec's own annotation) |
|---|---|---|
| 5 | `e2e_me_conflict_lifecycle.spec.ts` | seed fixture not present in this environment |
| 4 | `clawhub.spec.ts` | ClawHub service not running on port 5077 |
| 4 | `skillhub.spec.ts` | SkillHub service not running on port 5077 |
| 1 | `dwo_restart_durability.spec.ts` | opt-in: `ICDEV_E2E_DWO_RESTART=1` |
| 1 | `dwo_trigger_linkage.spec.ts` | opt-in: `ICDEV_E2E_DWO_TRIGGER=1` |

Thirteen of the fifteen are a service or fixture this deployment does not have;
a skip is an unmeasured test, not a passing one. The two opt-ins are deliberate.

## Artifacts

| What | Where |
|---|---|
| Structured build log | `.logs/build.ndjson` — one `playwright_run` event, `returncode 0, passed 838, failed 0, skipped 15, duration_s 1140.0` |
| Console transcript | `.tmp/pw_out.txt` |
| Screenshots | `.tmp/test_runs/playwright-artifacts/` (`screenshot: 'on'`) |
| Env diagnostics | `.tmp/test_runs/e2e-env-diagnostics.json` |

All of these are under `.tmp/` or `.logs/`, which are disposable and gitignored
— this document is the durable record. No JSON/HTML report was produced this
run (see the `--reporter=line` note above).

## Environment drift vs CI

`globalSetup`'s comparison against `.github/workflows/icdev-ci.yml → jobs.e2e`
reported **0 differ, 0 missing locally, 107 local-only** — the local `.env`
feature toggles a clean CI checkout does not set. Local run on `win32` /
Python 3.14 against CI's `ubuntu-latest` / Python 3.11 remains the standing
difference; a green sweep here is not a prediction of a green CI E2E job.
