# E2E Test: Network Design Canvas (NDC)

Verify the **Network Design Canvas** at `/network` — the platform's largest canvas
(391 routes) — renders and honors its recently-hardened contracts: topology CRUD
that actually persists, a fail-closed governance/AI-review verdict, deterministic
multi-persona traffic-flow walkthroughs, and a demo runner whose runs survive a
reload.

NDC lets an operator design DoD/IC network topologies, review them for SPOF /
redundancy / STIG gaps, model traffic flows across security zones, and drive
canned migration demos. This spec drives the real pages in a browser and asserts
the following regressions stay fixed:

- **ndc-sql-01** — `PUT /network/api/topologies/<id>` persists renames (was broken
  by a non-f-string `{_ph}` placeholder in the UPDATE).
- **ndc-sql-02** — the demo runner persists through the storage layer:
  `POST /network/api/demo-run` returns `{"ok": true}`, `GET /network/api/demo-runs`
  returns the run, and `/network/demo-runner` lists it in history.
- **ndc-gov-01** — governance/AI evaluation surfaces an explicit **error** state
  (amber `ERROR` finding / badge) instead of a fail-open "pass".
- **ndc-gov-02 / perf-01** — traffic-flow-walkthrough (TFW) persona narratives fall
  back to deterministic templates when no LLM is configured.

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session already owns `5050`, start your
  own instance on a free port with `python tools/dashboard/app.py --port 5099`
  and substitute that port everywhere below. This spec is written port-agnostic —
  read `<PORT>` as the port your dashboard is bound to.
- `ICDEV_NDC_ENABLED=true` (alias `ICDEV_NETWORK_ENABLED=true`) in `.env` — the
  canvas is gated behind this toggle; when unset the nav link is hidden and
  `/network` 404s.
- Database initialized (`ICDEV_STORAGE_BACKEND` default; NDC uses
  `NC_STORAGE_BACKEND`, PostgreSQL-primary). Tables `topologies`,
  `nc_traffic_flows`, `nc_intent_validations`, and `showcase_demo_runs` present.
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates and redirects home, so no manual key entry is
  needed; otherwise log in with the key on `/login` first.

### Seeding (so a fresh runner can pass every scenario)

- **Topologies (Scenarios 1–3):** the dashboard index lists up to 20 topologies
  from the `topologies` table. If empty, create one from the UI (`+ New Topology`
  on `/network`, or the `/network/templates` gallery → *Load*), or seed one over
  the API — **pass `graph_json` as a JSON object, never a pre-stringified string**
  (the create route `json.dumps()`-es the value; a double-encoded string is later
  read back as a non-graph and the AI review reports a fail-closed parse ERROR):

  ```bash
  curl -s -X POST http://127.0.0.1:<PORT>/network/api/topologies \
    -H 'Content-Type: application/json' \
    -d '{"name":"E2E Topo","graph_json":{"nodes":[
          {"id":"core1","label":"Core-1","type":"router"},
          {"id":"acc1","label":"Access-1","type":"switch"},
          {"id":"acc2","label":"Access-2","type":"switch"},
          {"id":"srv1","label":"Server-1","type":"server"},
          {"id":"iso1","label":"Orphan-1","type":"switch"}],
        "edges":[{"source":"core1","target":"acc1"},
                 {"source":"core1","target":"acc2"},
                 {"source":"acc1","target":"srv1"}]}}'
  ```

  This graph deliberately contains single-homed and isolated devices so the AI
  review returns HIGH/MEDIUM findings in Scenario 3.

- **Migration demo data (optional):** `python tools/network/seed_migration_demo.py`
  seeds demo projects, phases, and SOP links (safe to re-run).

- **Traffic flows (Scenario 4):** a flow is created via the API in-scenario. This
  requires the `nc_traffic_flows` table to expose the `TrafficFlowEngine` column
  names (`src_zone`, `dst_zone`, `app_type`). **Drift FIXED (ndc-fix-04):**
  `tools/network/db/init_db.py` now creates `nc_traffic_flows` with the engine's
  column names, and migration `223_traffic_flow_zone_rename.sql` renames the legacy
  `source_zone` / `destination_zone` / `application_type` columns on any pre-existing
  PG table. The DDL, engine, and all readers (`traffic_flow.py`, `narrative_generator.py`,
  `routes/twin_migration.py`, `routes/misc.py`) now agree, so `POST .../traffic-flows`
  no longer 500s and the Scenario 4 HTTP path runs directly.

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/ndc-e2e-<n>-<slug>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (accessibility tree / `page.content()`) and a screenshot named
> `playwright/screenshots/ndc-e2e-<n>-<slug>-FAIL.png` BEFORE aborting, so the
> failure is actionable (e.g. a 404 body proves the canvas route is not wired, and
> a 500 body proves a schema/column mismatch rather than a UI regression).

## Scenario 1 — /network dashboard renders (topology list + CUI banners)

### Login
1. Navigate to `http://127.0.0.1:<PORT>/login`. With `ICDEV_DASHBOARD_API_KEY`
   set, assert the redirect to home (`/`); otherwise fill the API key field, click
   "Login", and assert the redirect.

### Dashboard render
2. Navigate to `http://127.0.0.1:<PORT>/network`
3. Assert the response is HTTP 200 (NOT 404) and no traceback text is present
4. Assert the page heading contains "Network Design Canvas"
5. Assert the CUI banner "CUI // SP-CTI" is visible at the top of the dashboard
6. Assert the CUI banner "CUI // SP-CTI" is visible at the bottom of the dashboard
7. Assert the topology list is present — at least one topology card/link whose
   `href` matches `/network/canvas/<id>` (excluding the `+ New` /
   `/network/canvas/new` create links). If the list is empty, seed a topology per
   Prerequisites and reload.
8. Screenshot the dashboard as `playwright/screenshots/ndc-e2e-1-dashboard.png`
9. Check the browser console — assert no JavaScript errors on load

## Scenario 2 — Create + rename a topology, verify rename persists (ndc-sql-01)

10. Create a topology (either the `+ New Topology` UI flow, or
    `POST /network/api/topologies` with an object `graph_json` per Prerequisites).
    Capture the returned `id`; assert HTTP 201 and a non-empty `id`.
11. Navigate to `http://127.0.0.1:<PORT>/network/canvas/<id>`
12. Assert the canvas editor renders: the page title contains the topology name
    followed by "Canvas Editor", a drawing surface is present (an `svg` / JointJS
    paper element), and the AI-review toolbar button `#tb-ai-review-btn` exists.
    (Note: the full-bleed canvas editor does NOT carry the CUI banner — assert
    banners only on the dashboard/index, Scenario 1.)
13. Rename the topology: `PUT /network/api/topologies/<id>` with body
    `{"name":"<new name>"}`. Assert HTTP 200 and the response body is
    `{"ok": true}` (regression: a broken placeholder previously made this fail).
14. Confirm persistence at the data layer: `GET /network/api/topologies/<id>` and
    assert `name` equals the new name.
15. Confirm persistence in the UI: reload `http://127.0.0.1:<PORT>/network/canvas/<id>`
    and assert the page title now reflects the **new** name (not the original).
16. Screenshot the renamed canvas as
    `playwright/screenshots/ndc-e2e-2-canvas-rename.png`

## Scenario 3 — Governance / AI review: pass/fail findings + fail-closed error (ndc-gov-01)

Use the AI Topology Reviewer on the canvas — the reachable governance surface that
runs the fail-closed evaluation.

### Normal findings path
17. On `http://127.0.0.1:<PORT>/network/canvas/<id>` (a topology seeded with
    single-homed/isolated devices), click `#tb-ai-review-btn` to open the AI
    Review panel, then click the analyze button `#ai-review-run-btn`.
18. Wait for the review to complete. Assert the findings container
    `#ai-review-findings` renders at least one finding, and the status/summary text
    reports the count with severities (e.g. "N findings — X HIGH · Y MEDIUM"). The
    seeded graph should yield HIGH/MEDIUM findings in categories such as `SPOF`,
    `REDUNDANCY`, and `ROUTING`.
19. Equivalent API assertion (may be used instead of, or alongside, the UI):
    `POST /network/api/ai-review/<id>` returns HTTP 200 with `status: "ok"` and a
    non-empty `findings[]`, each carrying `severity`, `category`, `title`,
    `suggestion`.
20. Screenshot the rendered review panel as
    `playwright/screenshots/ndc-e2e-3-governance-aireview.png`

### Fail-closed error state (ndc-gov-01)
21. Assert the amber ERROR state is wired — an evaluation that cannot complete must
    surface as a distinct amber error, never a fail-open "healthy/pass". Assert
    **either** of the following (whichever the environment allows):
    - **Live (preferred):** induce an unparseable topology graph (e.g. create a
      topology whose `graph_json` was double-encoded to a string) and
      `POST /network/api/ai-review/<that-id>`; assert `status: "error"` and a
      finding with `severity: "ERROR"` (title "Topology graph could not be
      parsed"). Assert this is rendered amber, not green.
    - **Markup path:** assert the fail-closed error styling exists in the served
      page — the AI-review severity map contains the amber `ERROR` color
      (`_AI_REVIEW_SEV_COLORS` with `ERROR: '#e6a817'`) in `canvas.html`, and the
      intent-validation surface exposes the `badge-amber` / `⚠ ERROR` markup in
      `intent_validation.html`. This proves an evaluation error renders as a
      distinct amber not-pass state rather than a benign default.

## Scenario 4 — Traffic-flow walkthrough: deterministic persona narratives (ndc-gov-02 / perf-01)

22. Create a traffic flow on a seeded topology:
    `POST /network/api/twin/<id>/traffic-flows` with body
    `{"name":"E2E SSO Flow","src_zone":"user","dst_zone":"server","app_type":"sso_saml","classification":"NIPR"}`.
    Assert HTTP 201 and capture the returned flow `id`.
23. Run the walkthrough with the LLM disabled so the deterministic template
    fallback is exercised:
    `POST /network/api/twin/<id>/traffic-flows/<flow_id>/walkthrough` with body
    `{"use_llm": false}`. Assert HTTP 200.
24. Assert the walkthrough renders per-hop persona narratives — the response has a
    non-empty `steps[]`, and each step's `persona_responses` maps persona IDs
    (`seceng`, `neteng`, `cloudarch`, `compofficer`, `appdev`, `missionowner`,
    `ciso`) to a non-empty `narrative` string. The narrative text must be real
    prose (e.g. a `[SecEng]`-prefixed authentication description), NOT a blank or
    "LLM unavailable" placeholder — this proves the deterministic fallback.
25. Assert the walkthrough `summary` carries flow-level fields
    (`hop_count`, `classification`, `encryption`, `total_latency_ms`).
26. Screenshot the walkthrough (twin page `/network/twin/<id>` traffic-flow panel,
    or the walkthrough result) as
    `playwright/screenshots/ndc-e2e-4-tfw-walkthrough.png`

> **Drift note (Scenario 4) — FIXED (ndc-fix-04):** the `nc_traffic_flows` schema
> drift is resolved. `tools/network/db/init_db.py` now creates the table with the
> engine's column names (`src_zone` / `dst_zone` / `app_type`), and migration
> `223_traffic_flow_zone_rename.sql` renames the legacy
> `source_zone` / `destination_zone` / `application_type` columns on any pre-existing
> PG table. Step 22 (`POST .../traffic-flows`) therefore runs directly and must NOT
> be substituted with an engine-level fallback. If step 22 still returns HTTP 500
> with `column "src_zone" ... does not exist`, the migration has not been applied to
> that database — run the NDC migrations before treating it as a regression.

## Scenario 5 — Demo runner persists a run into history (ndc-sql-02)

27. Navigate to `http://127.0.0.1:<PORT>/network/demo-runner`; assert HTTP 200 and
    a "Run History" panel plus a "Total Runs" stat are present. Note the current
    "Total Runs" count.
28. Trigger a demo run: `POST /network/api/demo-run` with body
    `{"audience":"exec"}`. Assert HTTP 200 and the response body contains
    `"ok": true` and a non-empty `run_id`. (The run's internal scenario results may
    report `fail`; ndc-sql-02 is about **persistence**, not the demo content
    passing — do not assert scenario pass counts.)
29. Assert the run persisted: `GET /network/api/demo-runs` returns the run — a
    record whose `run_id` matches, with `audience`, `status`, and `created_at`.
30. Confirm persistence in the UI: reload
    `http://127.0.0.1:<PORT>/network/demo-runner` and assert the "Total Runs" stat
    incremented and the run history table (`#history-tbody`) now lists the run.
31. Screenshot the demo runner with history as
    `playwright/screenshots/ndc-e2e-5-demo-runner.png`
32. Check the browser console — assert no JavaScript errors accumulated across the
    session.

## Expected Results

- `/network` loads with HTTP 200 (gated on `ICDEV_NDC_ENABLED=true`), shows the CUI
  banners, and lists topologies.
- A created topology opens in the canvas editor; a `PUT` rename returns
  `{"ok": true}` and the new name survives a data-layer re-fetch AND a page reload
  (ndc-sql-01).
- The AI review returns findings with severities for a real topology, and an
  unparseable topology yields a distinct amber `ERROR` verdict — never a fail-open
  pass (ndc-gov-01).
- A traffic-flow walkthrough renders 7-persona narratives via the deterministic
  template fallback with `use_llm:false` (ndc-gov-02 / perf-01).
- A demo run returns `{"ok": true}`, is returned by `GET /network/api/demo-runs`,
  and appears in the demo-runner history after reload (ndc-sql-02).
- No JavaScript console errors across the session.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot.

## CUI Verification

- The `/network` dashboard (Scenario 1) displays "CUI // SP-CTI" in both the top
  and bottom banners.
- The full-bleed canvas editor and demo-runner pages intentionally omit the banner
  chrome — assert CUI banners only on the dashboard/index view.

## Screenshots

- `playwright/screenshots/ndc-e2e-1-dashboard.png`
- `playwright/screenshots/ndc-e2e-2-canvas-rename.png`
- `playwright/screenshots/ndc-e2e-3-governance-aireview.png`
- `playwright/screenshots/ndc-e2e-4-tfw-walkthrough.png`
- `playwright/screenshots/ndc-e2e-5-demo-runner.png`
