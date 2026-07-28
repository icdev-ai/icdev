# E2E Test: Durable Workflow Orchestration (DWO)

Verify the four capabilities the DWO card added to **Workflow Studio** hold end
to end in a real browser + API session: a run **survives a restart** and can be
resumed, an approval **gate produces exactly one reviewer-inbox row** (Studio is
a producer, workflow_hitl is the single inbox), a `node_type: mcp` step
**dispatches only allowlisted registry tools** (default-deny), and **run-scoped
memory** carries values between steps.

Feature doc: `docs/features/dwo-durable-workflow-orchestration.md`.
Companion pytest suites (unit/integration, run these first — a failure here
means the browser scenarios below cannot pass):
`tests/test_dwo_vv_durability.py`, `tests/test_dwo_vv_mcp_dispatch.py`,
`tests/test_dwo_vv_triggers.py`, `tests/test_dwo_gate_bridge.py`,
`tests/test_dwo_dur_03_resume_surface.py`.

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session already owns `5050`, start your
  own instance with `python tools/dashboard/app.py --port 5099` and substitute
  that port everywhere below. This spec is written port-agnostic — read
  `<PORT>` as the port your dashboard is bound to.
- Migrations `303_studio_run_memory.sql` and `304_studio_event_tables.sql`
  applied: tables `studio_run_memory`, `studio_event_sources`,
  `studio_workflow_triggers`, `studio_trigger_events` present, alongside the
  Studio run tables (`studio_workflow_runs`, `studio_workflow_run_steps`) and
  the HITL tables (`wf_external_steps`, `wf_instances`, `wf_templates`).
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates; otherwise log in with the key first.
- workflow_hitl enabled — `/workflow/` returns 503 when it is not, and the gate
  bridge then falls back to the Studio-only Details-modal gate (Scenario 3 has a
  documented degraded pass path for this).

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/dwo-e2e-<n>-<slug>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a DOM
> snapshot (accessibility tree / `page.content()`) and a screenshot named
> `playwright/screenshots/dwo-e2e-<n>-<slug>-FAIL.png` BEFORE aborting. A 404
> body proves the route is not wired; a 500 on a run API proves a runner/DB
> regression rather than a UI issue.

## Scenario 1 — Workflow Studio renders; run history is reachable

1. Navigate to `http://127.0.0.1:<PORT>/login` and assert the redirect home.
2. Navigate to `http://127.0.0.1:<PORT>/studio/workflows`. Assert HTTP 200
   (NOT 404 / 503), no traceback or error banner in the body, and the CUI
   banner "CUI // SP-CTI" is visible.
3. Assert `GET /api/studio/workflows` returns HTTP 200 with a JSON list, and
   `GET /api/studio/workflows/runs` returns HTTP 200 with a `runs` array
   (an empty array is a valid, honest result on a fresh DB).
4. Screenshot as `playwright/screenshots/dwo-e2e-1-studio-workflows.png`
5. Check the browser console — assert no JavaScript errors on load.

## Scenario 2 — A gated workflow parks, and the gate survives a restart

6. Create a workflow with three steps — an `mcp` step naming an allowlisted
   read-only tool, a `human` gate, then a terminal step — via
   `POST /api/studio/workflows`. Assert HTTP 201/200 and capture `workflow_id`.
7. Start it: `POST /api/studio/workflows/<workflow_id>/run`. Assert HTTP 200/202
   and capture `run_id`.
8. Poll `GET /api/studio/workflows/runs/<run_id>` (or read the SSE stream
   `GET /api/studio/workflows/runs/<run_id>/stream`) until the run reaches
   `awaiting_approval`. Capture the parked step's `step_run_id`.
9. Restart the dashboard process (or, when the runner cannot restart the server,
   assert the parked state is DB-resident: the `studio_workflow_run_steps` row
   is `status='awaiting_approval'` and no in-process `threading.Event` is needed
   to find it). Re-`GET` the run and assert it is STILL `awaiting_approval` —
   the gate did not evaporate with the worker thread (dwo-dur-01/02).
10. Screenshot the parked run in the Run History Details modal as
    `playwright/screenshots/dwo-e2e-2-gate-parked.png`

## Scenario 3 — ONE reviewer inbox: the gate appears in workflow_hitl (dwo-dur-04)

11. `GET /api/v1/wf/external`. Assert HTTP 200 and that EXACTLY ONE row has
    `external_system == "studio"` and `external_ref == "<step_run_id>"` from
    step 8. More than one row for the same `step_run_id` is a FAIL (re-parking
    must not duplicate the reviewer-inbox entry).
12. Navigate to `http://127.0.0.1:<PORT>/workflow/`. Assert HTTP 200 and the
    parked Studio gate is listed in the queue. Screenshot as
    `playwright/screenshots/dwo-e2e-3-reviewer-inbox.png`
    - **Degraded path (documented pass):** if workflow_hitl is disabled,
      `/workflow/` returns 503 and step 11 returns no `studio` row. Assert
      instead that the Studio Details modal still exposes the gate's
      Approve / Reject controls — the fallback is honest, not a crash.
13. Approve from the HITL side: `POST /api/v1/wf/external/<step_id>/complete`.
    Assert HTTP 200, and then that the STUDIO run left `awaiting_approval`
    (re-`GET /api/studio/workflows/runs/<run_id>`) — approving in one surface
    releases the other.
14. Post the SAME decision a second time. Assert it is REFUSED (not applied
    twice): the response reports failure/no-op and the run status is unchanged.

## Scenario 4 — Resume control: status parity and the 202/404/409 contract

15. Open Studio → Run History → **Details** for the run from Scenario 2.
    Assert the **▶ Resume Run** control is present while the run's status is
    resumable (`pending`, `running`, `awaiting_approval`, `failed`) and absent
    once the run is terminal (`success`).
16. `POST /api/studio/runs/<run_id>/resume` on a resumable run. Assert HTTP 202
    with `{"status":"resuming","mode":"in_place"}`.
17. `POST /api/studio/runs/<unknown-run-id>/resume`. Assert HTTP 404.
18. `POST /api/studio/runs/<run_id>/resume` on a run that has reached `success`.
    Assert HTTP 409 (terminal run — nothing to resume).
19. Screenshot the Details modal with the Resume control as
    `playwright/screenshots/dwo-e2e-4-resume-control.png`

## Scenario 5 — `node_type: mcp` is default-deny (dwo-mcp-02/03)

20. Assert the allowlisted MCP step from Scenario 2 executed: its step result
    carries the registry tool's output, not a refusal.
21. Create and run a workflow whose `mcp` step names a tool that is NOT in
    `mcp_workflow_tools.allowed` in `args/security_gates.yaml` (e.g. a
    deploy/delete tool, or a nonexistent name). Assert the step is REFUSED —
    the run does not succeed, the step result says the tool is not allowlisted,
    and the refusal is recorded rather than silently skipped.
22. Assert an `mcp` step declaring NO `mcp_tool` fails validation with the
    documented message (`node_type: mcp step declares no 'mcp_tool'`) rather
    than dispatching an empty tool name.
23. Screenshot the refused run as
    `playwright/screenshots/dwo-e2e-5-mcp-denied.png`

## Scenario 6 — Run-scoped memory survives the step boundary and the resume

24. Assert the run from Scenario 2 has `studio_run_memory` rows scoped to its
    `run_id` (write via an early step, read in a later one). Values written
    before the gate MUST still be readable after the resume in step 16 —
    memory is DB-backed, not worker-thread state.
25. Assert memory is run-scoped: a second run of the same workflow does not see
    the first run's keys.

## Expected Results

- `/studio/workflows` and `/workflow/` both render 200 with no error banner, and
  the run APIs return well-formed JSON.
- A `human` gate parks in the DB and is still `awaiting_approval` after a
  restart (dwo-dur-01/02).
- The parked gate produces EXACTLY ONE `wf_external_steps` row
  (`external_system='studio'`, `external_ref=<step_run_id>`); approving in
  either surface releases the other exactly once, and a second decision on a
  decided gate is refused (dwo-dur-04).
- The Resume control appears only for resumable statuses, and the resume API
  answers 202 / 404 / 409 per the documented contract (dwo-dur-03).
- An allowlisted `mcp` step dispatches; a non-allowlisted one is refused before
  dispatch (dwo-mcp-02/03).
- Run memory written before a gate is readable after the resume, and is not
  visible to another run (dwo-mem-01/02).
- No JavaScript console errors across the session.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot.

## CUI Verification

- `/studio/workflows` displays the "CUI // SP-CTI" banner.
- `/workflow/` (reviewer inbox) displays the "CUI // SP-CTI" banner.

## Screenshots

- `playwright/screenshots/dwo-e2e-1-studio-workflows.png`
- `playwright/screenshots/dwo-e2e-2-gate-parked.png`
- `playwright/screenshots/dwo-e2e-3-reviewer-inbox.png`
- `playwright/screenshots/dwo-e2e-4-resume-control.png`
- `playwright/screenshots/dwo-e2e-5-mcp-denied.png`
