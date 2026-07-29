# E2E Test: Durable Workflow Orchestration (DWO)

Verify in a real browser that a **Studio workflow run survives the process that
started it**. The headline claim of the DWO card — "a run parked on an approval
gate is still parked after the dashboard restarts, and can then be approved to
completion" — is proven in pytest (`tests/test_dwo_vv_durability.py`,
`tests/test_dwo_gate_durability.py`) but CLAUDE.md requires a Playwright pass on
any change that reaches the dashboard. This spec is that pass.

The run engine is `tools/studio/workflow_runner.py`; the surface is
`/studio/workflows` plus the `/api/studio/…` routes in
`tools/dashboard/api/studio.py`. The behaviours under test:

- **dwo-dur-01/02** — a `node_type: human` step parks the run at
  `awaiting_approval` and writes that state to `studio_workflow_runs` /
  `studio_workflow_run_steps`. The worker is a **daemon thread**, so a restart
  kills it and leaves only the DB rows. On the next boot
  `reconcile_runs_on_boot()` (called once from the app startup path,
  `tools/dashboard/app.py`) re-attaches every gate still inside its approval
  window, expires the ones past it, and fails steps orphaned at `running`.
- **dwo-dur-03** — `POST /api/studio/runs/<run_id>/resume` (alias
  `/api/studio/workflows/runs/<run_id>/resume`) continues the run **in place**
  (`RESUME_MODE == "in_place"`); it does not fork a new run row. Steps already
  recorded `success`/`approved`/`skipped` are replayed from the DB, not
  re-executed. The `▶ Resume Run` control is rendered by
  `static/js/workflow-studio-exec.js` for exactly the statuses in
  `RESUMABLE_RUN_STATUSES` (`pending`, `running`, `awaiting_approval`,
  `failed`).

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session already owns `5050`, start your
  own instance on a free port with `python tools/dashboard/app.py --port 5099`
  and substitute that port everywhere below. This spec is written port-agnostic
  — read `<PORT>` as the port your dashboard is bound to.
- **You must be able to restart the dashboard you are driving.** Scenario 2
  stops and restarts the process. Do not run this spec against a dashboard
  another session owns — start your own on a free port. (See the repo rule
  against blanket-killing processes: stop only the PID you started.)
- Studio tables present. `tools/studio/init_db.py` creates them
  (`STUDIO_TABLES`) and the app calls it at startup, so a fresh install is
  covered; `python tools/studio/init_db.py --json` is the manual path.
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates and redirects home, so no manual key entry is
  needed; otherwise log in with the key on `/login` first.
- `/studio/workflows` is registered unconditionally in `tools/dashboard/app.py`
  — there is no canvas toggle gating it, so no `.env` flag is required.

### Seeding (so a fresh runner can pass every scenario)

`POST /api/studio/workflows` accepts a bare `{name, steps: [...]}` body (it
YAML-dumps the steps for you when `template_yaml` is absent — this path exists
for E2E callers). Seed a two-step workflow whose second step is a human gate:

```json
{
  "name": "DWO E2E — gate durability",
  "description": "E2E fixture: one tool step, then a human gate.",
  "category": "custom",
  "steps": [
    {"id": "prep", "name": "Prep", "tool": "tools/testing/health_check.py",
     "args": ["--json"]},
    {"id": "gate", "name": "Human Gate", "node_type": "human",
     "approval_timeout": 3600}
  ]
}
```

- `approval_timeout` is in **seconds** and defaults to `86400` (24h) when
  omitted (`_GATE_DEFAULT_TIMEOUT`). Set it explicitly to something comfortably
  longer than the restart takes — an expired gate is failed, not resumed, by
  `reconcile_runs_on_boot()`, and that is a legitimate but different outcome.
- The response is `201 {"status": "ok", "workflow_id": "..."}`. Keep the
  `workflow_id`; `POST /api/studio/workflows/<workflow_id>/run` returns
  `202 {"run_id": "run-…"}`.
- `playwright.config.ts` wipes `outputDir` on every run. Screenshots below are
  written to `playwright/screenshots/` (the repo convention), which is not the
  Playwright output dir — anything that must survive belongs there.

## Scenario 1 — Studio renders and a run reaches the gate

1. Navigate to `http://localhost:<PORT>/login` and authenticate (auto-redirects
   home when `ICDEV_DASHBOARD_API_KEY` is set).
2. Navigate to `/studio/workflows`. Assert HTTP 200, that `#wf-studio-layout`
   and the `#wf-canvas` editor surface are present, and that the header reads
   "Workflow Studio". No error banner.
3. Screenshot as `playwright/screenshots/dwo-e2e-1-studio.png`.
4. Create the fixture workflow over the API (see Seeding). Assert `201` and
   capture `workflow_id`.
5. Start it: `POST /api/studio/workflows/<workflow_id>/run` with
   `{"project_id": "default"}`. Assert `202` and capture `run_id`.
6. Open the SSE stream `GET /api/studio/workflows/runs/<run_id>/stream` (or let
   the page's own stream drive) and wait for a `step_awaiting_approval` event.
   Poll `GET /api/studio/workflows/runs/<run_id>` until `status ==
   "awaiting_approval"` if the stream is not observed directly.
7. Assert the run detail shows the `Human Gate` step at `awaiting_approval`,
   with **Approve** / **Reject** controls rendered
   (`StudioWF.approveStep` / `StudioWF.rejectStep`). Capture the gate's
   `step_run_id` — it must be the *same* value after the restart in Scenario 2.
8. Screenshot the parked run as
   `playwright/screenshots/dwo-e2e-1-run-parked.png`.

## Scenario 2 — The gate survives a dashboard restart (dwo-dur-01/02/03)

This is the load-bearing scenario. It is the only one that actually restarts the
process; everything before it is setup.

9. **Pre-restart evidence.** Record `run_id`, the gate `step_run_id`, and the
   run's `status`. Screenshot as
   `playwright/screenshots/dwo-e2e-2-pre-restart.png`.
10. Stop **only the dashboard PID you started**. Do not sweep `python.exe` — a
    blanket kill takes out the kanban scheduler and any sibling session.
11. Start the dashboard again on the same port and wait for it to serve. On
    boot, `reconcile_runs_on_boot()` runs; the app log records
    `Studio runs reconciled: resumed=N expired=M` when either list is non-empty.
12. **Post-restart evidence.** Reload the run detail. Assert:
    - the run still exists and `status` is still `awaiting_approval` — it was
      **not** force-failed by the restart (the pre-dwo-dur-01 behaviour this
      spec exists to keep fixed);
    - the gate step's `step_run_id` is **unchanged** — resume is in place, so no
      second run row was forked and no approval was stranded;
    - the Approve / Reject controls are still rendered.
    Screenshot as `playwright/screenshots/dwo-e2e-2-post-restart.png`.
13. Approve the gate:
    `POST /api/studio/workflows/runs/<run_id>/steps/<step_run_id>/approve` with
    `{"actor": "e2e"}`. Assert `200 {"status": "approved"}`. A `404` here means
    the approval was already resolved or the gate timed out — treat it as a
    FAIL of this scenario and capture the run row, because it is exactly the
    stranded-approval failure mode the in-place resume design prevents.
14. Poll `GET /api/studio/workflows/runs/<run_id>` until the run reaches a
    terminal status. Assert it is `success`, and that the earlier `prep` step
    still reads its **original** result — replayed, not re-executed (the step's
    `started_at` is unchanged and no second row was inserted for it).
15. Screenshot the completed run as
    `playwright/screenshots/dwo-e2e-2-complete.png`.

## Scenario 3 — The `▶ Resume Run` control matches the API contract

16. Start a second run of the fixture workflow and let it park at the gate.
17. Assert the run detail modal renders the `▶ Resume Run` button
    (`StudioWF.resumeRun`) — `awaiting_approval` is in
    `RESUMABLE_RUN_STATUSES`, and the JS list is asserted against the Python
    constant by `tests/test_dwo_dur_03_resume_surface.py`.
18. Click it. Assert `POST /api/studio/workflows/runs/<run_id>/resume` returns
    `202 {"status": "resuming", "mode": "in_place"}`, **or** `409` when a live
    worker already owns the run — the single-owner guard on `_run_queues` is the
    correct answer, not an error. Both are passes; a `500` is not.
19. Approve the gate and let the run finish. Assert `GET
    /api/studio/workflows/runs` lists exactly the runs created by this spec —
    no forked `resumed_from_*` row appeared.
20. Screenshot as `playwright/screenshots/dwo-e2e-3-resume.png`.
21. Check the browser console — assert no JavaScript errors accumulated across
    the session.

## The three DWO V&V specs, and how CI runs them (dwo-vv-03-d5)

The scenarios above are implemented by three specs in `tests/e2e/`. Each is
opt-in behind its own env var, and `.github/workflows/icdev-ci.yml` runs all
three in the **"Run DWO V&V specs (dwo-vv-03-d5)"** step of the `e2e` job —
a separate `npx playwright test` invocation from the main sweep, because
`dwo_restart_durability` restarts a dashboard and the sweep drives one shared
long-lived server for ~800 tests.

| Spec | Opt-in var | Status on `main` (verified at `eabdde92d`, 2026-07-28) |
|---|---|---|
| `dwo_restart_durability.spec.ts` | `ICDEV_E2E_DWO_RESTART` | **passes** — needed migration 309's RLS columns on the run tables, and the spec pointing its child at PostgreSQL rather than the unmaintained sqlite fallback |
| `dwo_mcp_step_execution.spec.ts` | `ICDEV_E2E_DWO_MCP` | **passes** — `tools/studio/executors/mcp_executor.py` has merged, and migration 309 cleared the 500 from `GET /api/studio/workflows/runs/<id>` |
| `dwo_trigger_linkage.spec.ts` | `ICDEV_E2E_DWO_TRIGGER` | **skips at its probe** — see below |

Only the trigger spec is still blocked, and only on half of what it once was:
`tools/studio/event_dispatch.py` and the gateway's `dispatch_envelope_async`
hook have landed (dwo-evt-02), but `/api/studio/event-sources` and
`/api/studio/triggers` are still not registered in
`tools/dashboard/api/studio.py`, and `run.trigger_event` is not on the
run-detail payload. Those are **dwo-evt-04**. The spec probes for the surface
and skips precisely rather than reporting an unbuilt feature as a regression,
so the CI step is green today and starts exercising the trigger path for real
the moment dwo-evt-04 merges — with no edit to the workflow.

Run them locally against a dashboard you own:

```bash
ICDEV_E2E_DWO_RESTART=1 ICDEV_E2E_DWO_MCP=1 ICDEV_E2E_DWO_TRIGGER=1 \
ICDEV_NO_SERVER=1 ICDEV_STORAGE_BACKEND=postgresql \
  npx playwright test tests/e2e/dwo_restart_durability.spec.ts \
                      tests/e2e/dwo_mcp_step_execution.spec.ts \
                      tests/e2e/dwo_trigger_linkage.spec.ts
```

`ICDEV_STORAGE_BACKEND=postgresql` is not optional from a git worktree: a
worktree has no `.env`, so `load_dotenv` finds nothing and the backend falls
back to sqlite — a database nothing maintains, which is what kept the restart
spec red before. Set `ICDEV_PW_RUN_TAG=dwo` to keep this run's report and
artifacts out of the main sweep's paths.

## Expected Results

- `/studio/workflows` loads with HTTP 200 and renders the editor surface; no
  error banner, no JavaScript console errors.
- A workflow with a `node_type: human` step parks its run at
  `awaiting_approval` and exposes Approve / Reject controls.
- **After a full dashboard restart** the run is still `awaiting_approval` with
  the **same** `step_run_id` — not force-failed, not forked (dwo-dur-01/02).
- Approving the surviving gate drives the run to `success`, and the pre-restart
  step is replayed from the DB rather than re-executed (dwo-dur-03).
- The resume endpoint answers `202 {mode: "in_place"}` or a `409` single-owner
  refusal — never a 500 — and the `▶ Resume Run` control appears for exactly
  the statuses in `RESUMABLE_RUN_STATUSES`.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL`
  screenshot.

## CUI Verification

- `/studio/workflows` inherits the global classification banner from
  `base.html` (`includes/_banner.html`, configured by `args/banner.yaml` /
  `ICDEV_BANNER_MODE`). Assert the banner renders per the configured mode —
  do **not** assert a hard-coded "CUI // SP-CTI" string, which the Studio
  templates carry as a source-level marker, not as rendered chrome.

## Screenshots

- `playwright/screenshots/dwo-e2e-1-studio.png`
- `playwright/screenshots/dwo-e2e-1-run-parked.png`
- `playwright/screenshots/dwo-e2e-2-pre-restart.png`
- `playwright/screenshots/dwo-e2e-2-post-restart.png`
- `playwright/screenshots/dwo-e2e-2-complete.png`
- `playwright/screenshots/dwo-e2e-3-resume.png`

---

## The three DWO specs, and how to run them as a set (dwo-vv-03-d5)

The card ships three Playwright specs. All of them are **opt-in**, each for its
own reason, and the reasons are not interchangeable — read them before assuming
a skip is a failure.

| Spec | Flag | Owns a process? | Status |
|---|---|---|---|
| `tests/e2e/dwo_restart_durability.spec.ts` | `ICDEV_E2E_DWO_RESTART=1` | its own dashboard | **passes** |
| `tests/e2e/dwo_mcp_step_execution.spec.ts` | `ICDEV_E2E_DWO_MCP=1` | no — shared webServer | **passes** (on PostgreSQL) |
| `tests/e2e/dwo_trigger_linkage.spec.ts` | `ICDEV_E2E_DWO_TRIGGER=1` | its own gateway | **skips** — feature not built |

Run all three in one pass:

```bash
ICDEV_E2E_DWO_RESTART=1 ICDEV_E2E_DWO_MCP=1 ICDEV_E2E_DWO_TRIGGER=1 \
  npx playwright test tests/e2e/dwo_restart_durability.spec.ts \
                      tests/e2e/dwo_mcp_step_execution.spec.ts \
                      tests/e2e/dwo_trigger_linkage.spec.ts --reporter=line
```

Expected today: **2 passed, 1 skipped**.

### Why each is opt-in — these are not the same reason

- **restart** — it stops and starts a dashboard process. The CI sweep drives ONE
  long-lived dashboard shared by ~800 tests; restarting it would break every
  test running alongside. This is about **process ownership** and will not
  change. Run it where you own the dashboard.
- **mcp** — its original blockers (the unmerged `mcp_executor.py`, and the run
  tables' missing RLS columns) are **both fixed** — #976/#978/#979 and migration
  309 (#989). It is proven green against PostgreSQL. It stays gated only because
  it uses the shared webServer, which `playwright.config.ts` pins to **sqlite**
  while PostgreSQL is the primary backend. Un-gate it in **e2p-back-03**, which
  moves the suite to PG.
- **trigger** — skips on a *precise probe*, not a flag: it asks
  `GET /api/studio/event-sources` and skips when that is not 200. That route
  does not exist in the tree yet; it ships with **dwo-evt-04** (the triggers API
  and `run.trigger_event`, tasks `d3`/`d4`/`d5`). The spec is right to skip —
  asserting against an unbuilt feature would report it as a regression.

### What "all three pass in a single run" needs

Two things that are **not** this task:

1. `dwo-evt-04-d4`/`d5` — build the triggers API so the trigger spec has a
   surface to assert against.
2. `e2p-back-03` — move the E2E suite to PostgreSQL so the mcp spec can be
   un-gated and join the sweep.

Until both land, 2 passed / 1 skipped is the correct and complete result. A
green run here is not the same as three specs in CI, and this file should not
be read as claiming otherwise.
