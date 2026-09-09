# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-09 (task-e2e-c2e530db)

Full-suite run against all menus, use cases, canvases, and chat right panels.
Successor to [`e2e-full-smoke-2026-08-21.md`](e2e-full-smoke-2026-08-21.md).

## Results

| Outcome | Count |
|---|---|
| Passed | 824 |
| Failed | 2 |
| Skipped | 16 |
| Did not run | 1 |
| **Total** | **843** (67 spec files, all of `tests/e2e/`) |

Wall clock **17.5m**. Route smoke gate (step 1): **PASS, 88/88** — 78 nav routes
+ 10 API endpoints, no failures, so the full suite ran. Re-run after the suite:
88/88 again.

Against 2026-08-21 (807 passed / 5 failed / 26 skipped): **five failures down to
two**, and both of those two are one harness defect, fixed here and proven
fixed. **This run found no product defect.**

## How it was run

```bash
python tools/testing/route_smoke.py --all          # gate, 88/88

ICDEV_DASHBOARD_PORT=5091 \
ICDEV_E2E_BASE_URL=http://127.0.0.1:5091 \
ICDEV_PG_DATABASE=icdev_e2e \
  node node_modules/@playwright/test/cli.js test tests/e2e/ --project=chromium
```

Carried from the 2026-08-21 report and still true:

* **No `--reporter` override.** It REPLACES the config's reporter list and the
  html reporter then falls back to `playwright-report/`, which is tracked in git
  and which Playwright CLEARS on start. Passing none gives `list` + `json` +
  `html` in the right places, and the JSON is where the counts above come from.
* **Port probed free first.** 5090 — the port the config's own isolation example
  names — was already occupied by another session on this host, as it has been
  before. 5091 was free.
* **`icdev_e2e` is the throwaway database** (1827 tables, migrated; 57 non-empty
  tables). The canonical `icdev` board was not written to by the suite.

New this run:

* **The worktree carried no `node_modules`.** A directory junction onto the
  shared checkout's (`mklink /J node_modules C:\AI\ICDev\node_modules`) is
  enough — `testDir`, the reporters and `outputDir` all resolve from the config
  file's own `__dirname`, so the specs run from the worktree and the artifacts
  land there.
* **The second kanban scheduler was suppressed, not left to spawn.**
  `tools/dashboard/app.py`'s `__main__` block starts one unless
  `<BASE_DIR>/.tmp/kanban_scheduler.heartbeat` is fresher than 180s, and
  `BASE_DIR` resolves to the *worktree* (cwd precedes `PYTHONPATH` on
  `sys.path`), so a worktree run has no fresh heartbeat and spawns one against
  whatever database the run is on. A background process kept that file fresh for
  the duration — the watchdog restarts at >300s stale, so it has to be kept
  fresh, not merely created once.

## The two failures were ONE defect, and it is in the harness

| # | Spec | Failure |
|---|---|---|
| 1 | `dic_workspace_decisions.spec.ts:158` | `#dws-rail .dws-card` expected 3, **received 0** (30s) |
| 2 | `dic_workspace_two_reviewers.spec.ts:134` | `#dws-rail .dws-card[data-suggestion-id="…"]` not found (30s) |

The third test in file 1 (`:270`, a stale anchor shown as a refusal) is the "1
did not run" — a `describe.serial` cascade off #1, not a separate finding.

**The rail was reading a different database from the one the fixture seeded.**

`webServerDatabaseEnv()` redirects the dashboard Playwright starts. It does not
reach a subprocess a *spec* spawns, and both DIC workspace specs ran their
Python seed fixture with `env: { ...process.env, PYTHONIOENCODING: 'utf-8' }` —
carrying the operator's ambient `ICDEV_DATABASE_URL` through unchanged. Every
connection site in `tools/db/storage.py` reads that DSN **before** the discrete
`ICDEV_PG_DATABASE`, so under the documented isolation recipe the fixture
committed to the canonical `icdev` while the server served `icdev_e2e`.

Measured with exactly the env this run used:

```
$ ICDEV_PG_DATABASE=icdev_e2e python -c "from tools.db.storage import get_connection; ..."
FIXTURE would write to: icdev
$ curl -s http://127.0.0.1:5091/api/health
{"backend":"postgresql","database":"icdev_e2e","database_measured":true,"db":true,"status":"ok"}
```

That is qa-fail-6a87916931be3793's defect surviving one layer over. Its second
cost is worse than two red tests: **an "isolated" run was still writing fixture
rows into the canonical board** — the exact residue the isolation recipe exists
to prevent.

### The product is fine, and that is measured rather than assumed

Seeding the same fixture into the database the server is actually on:

```
$ ICDEV_DATABASE_URL= ICDEV_PG_DATABASE=icdev_e2e \
    python tests/e2e/fixtures/dic_workspace_fixture.py --seed
$ curl "http://127.0.0.1:5091/document-intelligence/api/change-set?doc_id=…"
{"changes":[ … 3 changes, anchor_verified:true, appliable:true … ]}
$ curl -o /dev/null -w "%{http_code}" "http://127.0.0.1:5091/document-intelligence/workspace/…"
200
```

Three changes, anchored and appliable — which is precisely what
`#dws-rail .dws-card` counts.

### The fix, and the proof it is the fix

`...webServerDatabaseEnv()` is spread into the subprocess env in both DIC specs
— the **same function** the config builds the server's env from, never a second
spelling of the precedence, because two spellings are how the two halves came to
disagree about one database in the first place. It returns `{}` when no database
was requested, so an ordinary local run is unchanged.

Re-run of both specs against the same server, the same database, nothing else
changed:

```
ok 1 dic_workspace_decisions.spec.ts:183  three changes accepted in one session, with no reload (5.4s)
ok 2 dic_workspace_decisions.spec.ts:295  a stale anchor is shown as a refusal, and the counts follow it (4.4s)
ok 3 dic_workspace_two_reviewers.spec.ts:159  a second reviewer's page learns, and their stale decision is refused (8.9s)
3 passed (23.0s)
```

Three passed, including the one that previously did not run at all.

### The same shape at two more sites

`tests/e2e/dwo_restart_durability.spec.ts` spawns a **whole second dashboard**
with `{ ...process.env, ...DASHBOARD_ENV }`, and that block pinned
`ICDEV_STORAGE_BACKEND` while saying nothing about the database — so an isolated
run would have started a dashboard on the canonical `icdev` and written its
runs, gates and workflows there. Fixed the same way, from the same function.

`tests/e2e/dwo_trigger_linkage.spec.ts` spawns a gateway child on a bare
`process.env` inherit and carries the identical exposure. **Not fixed here, and
named rather than implied:** it is opt-in behind `ICDEV_E2E_DWO_TRIGGER`, it was
skipped by this run, and a change to it cannot be verified by anything this run
executed. Fixing it blind is how an unverified edit ships.

## Skips — 16, every one accounted for

| Count | Spec | Reason |
|---|---|---|
| 4 | `clawhub.spec.ts` | ClawHub service not running on port 5077 |
| 4 | `skillhub.spec.ts` | SkillHub service not running on port 5077 |
| 5 | `e2e_me_conflict_lifecycle.spec.ts` | seed session fixture absent on this database |
| 1 | `dwo_restart_durability.spec.ts` | opt-in: `ICDEV_E2E_DWO_RESTART=1` |
| 1 | `dwo_trigger_linkage.spec.ts` | opt-in: `ICDEV_E2E_DWO_TRIGGER=1` |
| 1 | `nav_regression_probes.spec.ts` | no seeded pulse post on this database |

Fourteen of the sixteen are a service or a fixture this deployment does not
have, and they are **not a clean bill of health for those surfaces** — a skip is
an unmeasured test, not a passing one (`args/ci_skip_census.txt`'s rule, one
layer out). The two opt-ins are deliberate. The `e2e_me_conflict_lifecycle` five
and the pulse probe are a consequence of running against a nearly-empty
throwaway database, which is the price of not writing to the canonical board.

## Residue observed on `icdev_e2e`, unexplained

Six `dic_doc_ws03e2e_*` documents dated 2026-09-08 21:2x–21:3x sit in
`icdev_e2e`, each still carrying its three sections — a previous session's seeds
whose `afterAll` teardown did not run or did not complete. A seventh, dated
during this run, carried a `dic_documents` row with **zero** versions, sections
or suggestions, and had disappeared from a repeat query minutes later. Neither
is explained by the database split proven above, and neither was chased further
here. Recorded so the next run can tell accumulation from a one-off; not
repaired, because a teardown defect wants its own reproduction rather than a
guess made from the side of a smoke run.

## Artifacts

* stdout: `.tmp/pw_out.txt`
* JSON report: `.tmp/test_runs/playwright-results.json`
* HTML report: `npx playwright show-report .tmp/test_runs/playwright-report`
* screenshots + video for both failures, under
  `.tmp/test_runs/playwright-artifacts/`
* captured to the structured build log via
  `tools.logging.build_logger.capture_playwright`

# CUI // SP-CTI
