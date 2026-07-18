# E2E Test: Observability Design Canvas (ODC)

Verify the **Observability Design Canvas** at `/observability` renders across all
of its pages and honors its recently-hardened contracts: a kill-chain force-graph
that reads its knowledge-graph through the RLS-bypassing canvas connection (never a
500), and an OTel instrumentation twin whose telemetry snapshots actually persist
through a page reload.

ODC lets an operator design OpenTelemetry observability topologies, assess them for
detection coverage / MITRE ATT&CK visibility, browse SOPs and runbooks, model a
war-domain kill-chain graph, and simulate collector changes on an instrumentation
digital twin. This spec drives the real pages in a browser and asserts the
following regressions stay fixed:

- **obx-fix-01** — the kill-chain page (`/observability/kill-chain`) and its API
  (`GET /observability/api/kill-chain`) read the canvas-namespaced KG tables
  (`canvas_kg_nodes` / `canvas_kg_edges`, which have no `tenant_id`/`classification`
  columns) through `get_canvas_connection()`. Previously they used the global
  `get_connection()`, so the RLS predicate raised `UndefinedColumn` and the API
  500'd. The API must now return a valid `{"nodes": [...], "links": [...]}` payload
  — the D3 graph renders nodes when KG data exists, OR shows the honest empty-state
  (stats read `0`, no error banner) when the `sg` canvas has no nodes.
- **obx-fix-02** — the observability twin (`/observability/twin/<id>`) reads and
  writes snapshots through the *same* canvas code path (`twin.take_snapshot` /
  `twin.list_snapshots`), so a snapshot taken via
  `POST /observability/api/twin/<id>/snapshot` survives a reload of the twin page
  (read DB == write DB). Previously read/write diverged and snapshots vanished.

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session already owns `5050`, start your
  own instance on a free port with `python tools/dashboard/app.py --port 5099`
  and substitute that port everywhere below. This spec is written port-agnostic —
  read `<PORT>` as the port your dashboard is bound to.
- `ICDEV_OBSERVABILITY_ENABLED=true` in `.env` — the canvas is gated behind this
  toggle; when unset the nav link is hidden and `/observability` 404s.
- Database initialized (`ICDEV_STORAGE_BACKEND` default). Tables
  `observability_designs`, `od_assessments`, `od_templates`, `od_snapshots`, and
  the canvas KG tables `canvas_kg_nodes` / `canvas_kg_edges` present (the
  kill-chain route `CREATE TABLE IF NOT EXISTS`-es the KG tables on first hit, so a
  missing KG table degrades to the empty-state, not a 500).
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates and redirects home, so no manual key entry is
  needed; otherwise log in with the key on `/login` first.

### Seeding (so a fresh runner can pass every scenario)

- **Designs (Scenarios 2–4):** the dashboard index lists observability designs from
  the `observability_designs` table. Scenario 2 creates one over the API
  (`POST /observability/api/designs`) — pass `graph_json` as a JSON **string** (the
  create route stores the value verbatim; the default is `{"nodes":[],"edges":[]}`).
  To exercise real coverage/remediation scores, seed a design whose graph carries a
  few observability sources (e.g. `src-app-log`, `src-metric`, `src-trace` nodes)
  so the assessment returns non-zero coverage and at least one gap.
- **Kill-chain KG (Scenario 5):** the graph reads nodes/edges from the `sg` canvas
  in `canvas_kg_nodes` / `canvas_kg_edges`. If those tables are empty, the API
  honestly returns `{"nodes": [], "links": []}` and the page shows the zero-state
  (this is the documented pass condition for an unseeded environment — see
  Scenario 5). To exercise the populated path, run the STIX importer / temporal
  correlator that writes `canvas='sg'` KG rows, then reload.

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/odc-e2e-<n>-<slug>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (accessibility tree / `page.content()`) and a screenshot named
> `playwright/screenshots/odc-e2e-<n>-<slug>-FAIL.png` BEFORE aborting, so the
> failure is actionable (e.g. a 404 body proves the canvas route is not wired, and
> a 500 body on `/observability/api/kill-chain` proves an RLS/column regression
> rather than a UI issue).

## Scenario 1 — Page sweep: every ODC page renders without an error banner

### Login
1. Navigate to `http://127.0.0.1:<PORT>/login`. With `ICDEV_DASHBOARD_API_KEY`
   set, assert the redirect to home (`/`); otherwise fill the API key field, click
   "Login", and assert the redirect.

### Dashboard render
2. Navigate to `http://127.0.0.1:<PORT>/observability/`
3. Assert the response is HTTP 200 (NOT 404) and no traceback / error-banner text
   is present (no "store unavailable", no Python traceback in the body).
4. Assert the page heading identifies the Observability Design Canvas.
5. Assert the CUI banner "CUI // SP-CTI" is visible on the dashboard.
6. Screenshot the dashboard as `playwright/screenshots/odc-e2e-1-dashboard.png`
7. Check the browser console — assert no JavaScript errors on load.

### Static page sweep
8. For EACH of the following pages, navigate to it and assert HTTP 200 with no
   error banner / traceback in the body:
   - `http://127.0.0.1:<PORT>/observability/templates`  (template gallery)
   - `http://127.0.0.1:<PORT>/observability/assessments`  (assessment history)
   - `http://127.0.0.1:<PORT>/observability/canvas/new`  (canvas editor —
     assert the drawing surface `#canvas-container` and `.dc-toolbar` are present)
   - `http://127.0.0.1:<PORT>/observability/mitre`  (MITRE ATT&CK matrix)
   - `http://127.0.0.1:<PORT>/observability/sops`  (SOP library)
   - `http://127.0.0.1:<PORT>/observability/runbooks`  (runbook library)
9. Screenshot the canvas editor as `playwright/screenshots/odc-e2e-1-canvas-new.png`

## Scenario 2 — Create a design, then assess it

10. Create a design: `POST /observability/api/designs` with body
    `{"name":"E2E ODC Design","graph_json":"{\"nodes\":[{\"id\":\"log1\",\"type\":\"src-app-log\",\"label\":\"App Logs\"},{\"id\":\"m1\",\"type\":\"src-metric\",\"label\":\"Metrics\"}],\"edges\":[]}"}`.
    Assert HTTP 201 and capture the returned non-empty `id`.
11. Navigate to `http://127.0.0.1:<PORT>/observability/canvas/<id>`; assert HTTP 200
    and the page title contains the design name.
12. Run an assessment: `POST /observability/api/designs/<id>/assess` with body `{}`.
    Assert HTTP 200 and a response carrying `assessment` (with numeric `score` and a
    `grade`), `coverage`, `mitre_detection`, and a `gaps` list.
13. Screenshot the canvas as `playwright/screenshots/odc-e2e-2-canvas.png`

## Scenario 3 — Coverage + remediation pages render scores

14. Navigate to `http://127.0.0.1:<PORT>/observability/coverage/<id>` (the design
    from Scenario 2). Assert HTTP 200. The page runs the assessment client-side and
    populates the coverage gauge: assert `#covScore` resolves to a numeric percentage
    (e.g. matches `/\d+%/`), NOT the loading placeholder `--%`, and the coverage grid
    `#covGrid` becomes visible (the `#covLoading` state clears).
15. Screenshot the coverage page as
    `playwright/screenshots/odc-e2e-3-coverage.png`
16. Navigate to `http://127.0.0.1:<PORT>/observability/remediation/<id>`. Assert
    HTTP 200 and the loading state (`#remLoading`) clears — either the remediation
    cards (`#remCards`) render at least one gap card, or the empty-state
    (`#remEmpty`, "No gaps or findings detected") is shown. Both are honest
    outcomes; assert one of them is visible (the page is not stuck loading).
17. Screenshot the remediation page as
    `playwright/screenshots/odc-e2e-3-remediation.png`

## Scenario 4 — Twin snapshot persists across a reload (obx-fix-02)

18. Navigate to `http://127.0.0.1:<PORT>/observability/twin/<id>` (the design from
    Scenario 2). Assert HTTP 200 and the "Telemetry Snapshot" panel (`#snapPanel`)
    is present. Note the current number of `.snap-item` entries in `#snapList`
    (may be zero — the empty-state text "No snapshots yet" is shown).
19. Take a snapshot: `POST /observability/api/twin/<id>/snapshot` with body
    `{"label":"e2e-snap"}`. Assert HTTP 201 and a response carrying a non-empty
    `id`, the `label`, a numeric `service_count`, and a `coverage_score`. (An
    unknown design returns 404 and a persist failure returns 500 — a 201 proves the
    snapshot was written.)
20. Reload `http://127.0.0.1:<PORT>/observability/twin/<id>` and assert the snapshot
    PERSISTED: `#snapList` now contains a `.snap-item` whose text includes the new
    label ("e2e-snap"), and the count of `.snap-item` entries increased by one
    versus step 18. This proves read DB == write DB (the regression: snapshots
    previously vanished on reload because read/write used divergent connections).
21. Screenshot the twin page with the persisted snapshot as
    `playwright/screenshots/odc-e2e-4-twin-snapshot.png`

## Scenario 5 — Kill-chain graph: no 500, renders nodes or honest empty-state (obx-fix-01)

22. Navigate to `http://127.0.0.1:<PORT>/observability/kill-chain`. Assert HTTP 200,
    the D3 `<svg id="kc-svg">` container is present, and no error banner / traceback
    is in the body.
23. Assert the kill-chain API does NOT 500 (the obx-fix-01 regression): the page's
    boot `loadGraph()` calls `GET /observability/api/kill-chain?limit=200`. Fetch it
    directly (or read the network response) and assert HTTP 200 with a JSON body
    shaped `{"nodes": [...], "links": [...]}` (both keys present, both arrays). A
    500 or an `UndefinedColumn` error body is a FAIL.
24. Assert the D3 graph reflects the payload honestly — one of:
    - **Populated (preferred when `sg` KG data exists):** `#stat-nodes` shows a
      count > 0, the status line `#kc-status` reads "N nodes · M edges", and the
      SVG renders node circles (at least one `#kc-svg .kc-nodes g circle`).
    - **Empty-state (unseeded environment):** the API returned `{"nodes": [],
      "links": []}`, `#stat-nodes` and `#stat-edges` read `0`, `#kc-status` reads
      "0 nodes · 0 edges", and NO error banner is shown. This zero-state (not a
      crash) is the documented pass condition when the `sg` canvas has no KG rows.
25. Screenshot the kill-chain graph as
    `playwright/screenshots/odc-e2e-5-kill-chain.png`
26. Check the browser console — assert no JavaScript errors accumulated across the
    session.

## Expected Results

- Every ODC page (`/observability/`, `/templates`, `/assessments`, `/canvas/new`,
  `/mitre`, `/sops`, `/runbooks`, `/kill-chain`) loads with HTTP 200 and no error
  banner (gated on `ICDEV_OBSERVABILITY_ENABLED=true`).
- A created design opens in the canvas editor; an assessment returns a numeric
  score + grade, and the `/coverage/<id>` and `/remediation/<id>` pages render those
  scores/gaps rather than staying stuck on their loading state.
- A twin snapshot taken via `POST .../twin/<id>/snapshot` returns 201 and survives a
  reload of `/observability/twin/<id>` (obx-fix-02).
- `GET /observability/api/kill-chain` returns HTTP 200 with a `{nodes, links}`
  payload — never a 500 / `UndefinedColumn` — and the D3 graph renders nodes when
  KG data exists or an honest zero-state when it does not (obx-fix-01).
- No JavaScript console errors across the session.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot.

## CUI Verification

- The `/observability/` dashboard (Scenario 1) displays the "CUI // SP-CTI" banner.
- The full-bleed canvas editor (`/observability/canvas/new`) intentionally omits the
  banner chrome — assert the CUI banner on the dashboard/index view, not the editor.

## Screenshots

- `playwright/screenshots/odc-e2e-1-dashboard.png`
- `playwright/screenshots/odc-e2e-1-canvas-new.png`
- `playwright/screenshots/odc-e2e-2-canvas.png`
- `playwright/screenshots/odc-e2e-3-coverage.png`
- `playwright/screenshots/odc-e2e-3-remediation.png`
- `playwright/screenshots/odc-e2e-4-twin-snapshot.png`
- `playwright/screenshots/odc-e2e-5-kill-chain.png`
