# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-12 (task-e2e-8abb2f4d)

Full-suite run against all menus, use cases, canvases, and chat right panels.
Successor to [`e2e-full-smoke-2026-09-09.md`](e2e-full-smoke-2026-09-09.md).

## Results

| Outcome | Count |
|---|---|
| Passed | 838 |
| Failed | **0** |
| Skipped | 15 |
| Did not run | 0 |
| **Total** | **853** (69 spec files executed, out of 83 entries in `tests/e2e/`) |

Wall clock **19.9m** (1196.1s), exit code **0**. Route smoke gate (step 1):
**PASS, 88/88** — 79 nav routes + 9 API endpoints, no failures, so the full
suite ran. Re-run after the suite against the shared `:5050` dashboard: **88/88
again**.

Against 2026-09-09 (824 passed / 2 failed / 16 skipped / 1 did not run):
**the two failures and the cascade are gone**, the DIC workspace fix landed
there is proven across a full sweep rather than a three-spec re-run, and the
suite grew by ten executed tests. **This run found no product defect and no
harness defect.**

The 853 − 838 − 15 arithmetic closes exactly: there is no "did not run" bucket
this time, which is what a `describe.serial` cascade produces and what the
previous run had one of.

## How it was run

```bash
python tools/testing/route_smoke.py --all          # gate, 88/88

ICDEV_DASHBOARD_PORT=5093 \
ICDEV_E2E_BASE_URL=http://127.0.0.1:5093 \
ICDEV_PG_DATABASE=icdev_e2e \
  node node_modules/@playwright/test/cli.js test tests/e2e/ --project=chromium

python tools/testing/route_smoke.py --all          # post-suite, 88/88
```

Carried from the previous two reports and still true:

* **No `--reporter` override**, despite the card's step 2 naming
  `--reporter=line`. A `--reporter` flag REPLACES the config's reporter list,
  and the html reporter then falls back to `playwright-report/`, which is
  tracked in git and which Playwright CLEARS on start. Passing none gives
  `list` + `json` + `html` in the right places, and the JSON at
  `.tmp/test_runs/playwright-results.json` is where every count above comes
  from — `stats.expected` / `stats.unexpected` / `stats.skipped`, not a regex
  over console text.
* **`icdev_e2e` is the throwaway database** (1,826 tables, migrated). The
  canonical `icdev` board (1,841 tables) was not the target.
* **The worktree carried no `node_modules`.** A directory junction onto the
  shared checkout's (`mklink /J node_modules C:\AI\ICDev\node_modules`) is
  enough — `testDir`, the reporters and `outputDir` all resolve from the config
  file's own `__dirname`, so the specs ran from the worktree and the artifacts
  landed there.
* **The worktree carried no `.env` either**, so one was copied in before the
  run. Without it `ICDEV_STORAGE_BACKEND` is unset and the run would have taken
  the SQLite fallback path — a backend CI has never exercised and which the
  config's own e2p-back-03 note argues against at length.
* **No second kanban scheduler was spawned.** `tools/dashboard/app.py`'s
  `__main__` starts one unless `<BASE_DIR>/.tmp/kanban_scheduler.heartbeat` is
  fresher than 180s, and `BASE_DIR` resolves to the *worktree*. A background
  process refreshed that file every 30s for the duration (the watchdog restarts
  at >300s stale, so it has to be kept fresh, not merely created once).
  Verified after the run: exactly one `kanban_scheduler.py` on this host, pid
  30160, the pre-existing main-checkout one.

## The port caveat the last run left open is CLOSED

The 2026-09-09 report ended with: *"Next run: require exactly ONE listener and
identify it by command line before trusting the port, rather than only probing
it free."* Done, and it is the one procedural change here.

Ports 5090–5105 were probed; 5093 was chosen over the config's documented 5090
and the previous run's 5091 precisely because those two are the values a peer
session is most likely to reach for. After the server came up, the listener was
enumerated rather than assumed:

```
> Get-NetTCPConnection -LocalPort 5093 -State Listen
127.0.0.1 pid=32440 name=python                       # exactly one row

> (Get-CimInstance Win32_Process -Filter "ProcessId=32440").CommandLine
python C:\AI\ICDev\.tmp\worktrees\task-e2e-8abb2f4d\tools\dashboard\app.py
```

One listener, and it is **this run's own process running this worktree's tree**
— not a peer's, not the main checkout's. That is the property the previous run
could not state, and it is what makes "838 passed" a measurement of one server
this run controlled end to end.

## Database isolation held for the SPECS' subprocesses too

The previous run's two failures were fixture writers inheriting the ambient
`ICDEV_DATABASE_URL` while the server was redirected — an "isolated" run
writing fixture rows into the canonical board. `globalSetup` now measures both
halves rather than inferring one from the other, and both answered:

```
  ✓ E2E database confirmed (server):     measured on 'icdev_e2e' (via ICDEV_PG_DATABASE)
  ✓ E2E database confirmed (subprocess): measured on 'icdev_e2e' (via ICDEV_PG_DATABASE)
```

and the running server agreed when asked directly:

```
$ curl -s http://127.0.0.1:5093/api/health
{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}
```

Server *and* spawned writers on the throwaway. No E2E residue on the canonical
board from this run.

## Skips — 15, every one accounted for

| Count | Spec | Reason (the spec's own annotation) |
|---|---|---|
| 5 | `e2e_me_conflict_lifecycle.spec.ts` | `Seed session sess-9cc6891cb548 fixture (requirements) not present in this environment` |
| 4 | `clawhub.spec.ts` | `ClawHub service not running on port 5077 — skipping` |
| 4 | `skillhub.spec.ts` | `SkillHub service not running on port 5077 — skipping` |
| 1 | `dwo_restart_durability.spec.ts` | opt-in: `ICDEV_E2E_DWO_RESTART=1` — restarts a dashboard process it owns |
| 1 | `dwo_trigger_linkage.spec.ts` | opt-in: `ICDEV_E2E_DWO_TRIGGER=1` — spawns a gateway process it owns |

Thirteen of the fifteen are a service or a fixture this deployment does not
have, and they are **not a clean bill of health for those surfaces** — a skip is
an unmeasured test, not a passing one (`args/ci_skip_census.txt`'s rule, one
layer out). The two opt-ins are deliberate.

One skip from the previous run is **gone**: `nav_regression_probes.spec.ts`
skipped on 2026-09-09 for want of a seeded pulse post and RAN here. That is the
whole of the 16 → 15 movement; nothing newly skipped.

## Coverage the card asks about, named

The card asks for "all menus, use cases, canvases, and chat right panels". The
69 executed spec files include, by name:

* **Menus / nav** — `nav_smoke`, `nav_menu_readiness`, `nav_ops_build`,
  `nav_intelligence_compliance`, `nav_honesty_banners`, `nav_regression_probes`
* **Chat and its right panels** — `chat`, `chat_right_panels`,
  `chat_use_cases`, `chat_use_case_flows`, `chat_error_handling`
* **Canvases** — `canvas_smoke`, `canvases_extended`, `mission_canvas`,
  `noc_canvas`, `slides_canvas`, plus the per-canvas specs (`cortex`, `dsoc`,
  `migration_network_config_map`, `genesis`, `finetune`, `filesync`,
  `knowledge_search`, `kanban_pipeline`, the four `cpmp_*`, the five
  `govcon_*`, …)

83 entries exist under `tests/e2e/`; 69 contributed executed tests. The
remainder are not specs — `conftest.py`, `a11y_baseline.json`, the `fixtures/`
and `helpers/` directories, and two standalone Python drivers
(`e2e_govlift_lifecycle.py`, `e2e_migration_canvas.py`) that Playwright does not
collect. **Those two Python drivers were NOT run here**, and saying so matters:
the count above is 69 spec files, not "everything in the directory".

## Artifacts

| What | Where |
|---|---|
| Structured build log | `.logs/build.ndjson` — one `playwright_run` event, `returncode 0, passed 838, failed 0, skipped 15, duration_s 1196.06` |
| JSON reporter output | `.tmp/test_runs/playwright-results.json` |
| HTML report | `.tmp/test_runs/playwright-report/` (`npx playwright show-report .tmp/test_runs/playwright-report`) |
| Screenshots | 624 PNGs under `.tmp/test_runs/playwright-artifacts/` (`screenshot: 'on'`) |
| Env diagnostics | `.tmp/test_runs/e2e-env-diagnostics.json` |
| Console transcript | `.tmp/pw_out.txt` |

All of these are under `.tmp/`, which is disposable and gitignored — this
document is the durable record.

## Environment drift vs CI, unchanged and still worth stating

`globalSetup`'s comparison against `.github/workflows/icdev-ci.yml → jobs.e2e`
reported **0 differ, 0 missing locally, 107 local-only**. The 107 are `.env`
feature toggles a clean CI checkout does not set; the two files present here and
not in CI are `.env` itself and nothing else. Local runs on `win32` /
Python 3.14 against CI's `ubuntu-latest` / Python 3.11 remains the standing
difference — a defect CI catches and a local sweep does not is a live
possibility, and a green sweep here is not a prediction of a green CI E2E job.
