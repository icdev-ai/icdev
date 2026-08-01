# E2E Test: BI Studio (BI Dashboard Canvas)

Verify the **BI Dashboard Canvas** ("BI Studio") at `/bi_dashboard` — the
natural-language chart/dashboard builder — renders, ingests an uploaded CSV,
drafts a chart spec from a plain-English prompt, saves it as a dashboard, and
survives a reload. This spec also asserts the recently-hardened access
contracts stay fixed:

- **cnr-bi-01** — dashboard reads/mutations are owner-scoped: a user only sees
  their own dashboards and cannot GET/PUT/DELETE another user's dashboard
  (404 on cross-owner reads, 403 on cross-owner mutations).
- **cnr-bi-02** — the upload route enforces `ALLOWED_UPLOAD_EXTENSIONS`
  (`.csv`, `.json`, `.xlsx`) and returns HTTP 400 on a disallowed type; datasets
  and dashboards are threaded with the caller's tenant.

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session owns `5050`, start your own on a
  free port with `python tools/dashboard/app.py --port 5099` and read `<PORT>`
  as your bound port everywhere below.
- `ICDEV_BI_DASHBOARD_ENABLED=true` (default true) in `.env` — the canvas is
  gated behind this toggle; when disabled `/bi_dashboard` 404s and the blueprint
  is not registered.
- Database initialized (`ICDEV_STORAGE_BACKEND` default). Tables `bi_dashboards`,
  `bi_data_sources`, and `bi_generation_log` present (created lazily by the
  blueprint's `init_db()` on first request).
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates and redirects home; otherwise log in with the
  key on `/login` first.

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/bi-e2e-<n>-<slug>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (`page.content()`) and a screenshot named
> `playwright/screenshots/bi-e2e-<n>-<slug>-FAIL.png` BEFORE aborting (a 404 body
> proves the canvas is not wired; a 401 proves the login step did not persist).

## Scenario 1 — /bi_dashboard renders

### Login
1. Navigate to `http://127.0.0.1:<PORT>/login`. With `ICDEV_DASHBOARD_API_KEY`
   set, assert the redirect to home (`/`); otherwise fill the API key field,
   click "Login", and assert the redirect.

### Dashboard render
2. Navigate to `http://127.0.0.1:<PORT>/bi_dashboard`
3. Assert HTTP 200 (NOT 404) and no traceback text is present.
4. Assert the page heading contains "BI Studio".
5. Assert the CUI banner "CUI // SP-CTI" is visible.
6. Assert the upload input `#bidFile`, prompt textarea `#bidPrompt`, and the
   `#bidGenerateBtn` / `#bidSaveBtn` buttons are present.
7. Screenshot as `playwright/screenshots/bi-e2e-1-studio.png`.
8. Check the browser console — assert no JavaScript errors on load.

## Scenario 2 — Upload a CSV (cnr-bi-02 allowlist)

9. Upload a valid CSV via the API (or the `#bidFile` picker + `#bidUploadBtn`):
   ```bash
   printf 'region,sales\nEast,100\nWest,200\nEast,50\n' > /tmp/bi_sales.csv
   curl -s -b <session-cookie> -X POST \
     http://127.0.0.1:<PORT>/bi_dashboard/api/upload \
     -F 'file=@/tmp/bi_sales.csv'
   ```
   Assert HTTP 200 and `{"success": true, "id": "<source_id>"}`; capture
   `source_id`.
10. Assert the disallowed-extension guard: POST the same route with an
    `evil.sh` file and assert HTTP **400** with an `"unsupported file type"`
    error (the file must never reach the parser).
11. Assert `GET /bi_dashboard/api/datasets` returns the uploaded dataset
    (`name` "bi_sales" / row_count 3) and, in the UI, the source appears in the
    `#bidSourceSelect` dropdown after reload.
12. Screenshot as `playwright/screenshots/bi-e2e-2-upload.png`.

## Scenario 3 — Generate a chart from a prompt

13. Select the uploaded source, type "show sales by region as a bar chart" into
    `#bidPrompt`, and click `#bidGenerateBtn` (or
    `POST /bi_dashboard/api/generate` with body
    `{"prompt":"show sales by region as a bar chart","source_id":"<source_id>"}`).
14. Assert HTTP 200 and the response carries a `spec` (with `kind`), a `method`
    (`llm` | `llm_retry` | `heuristic`), and a `structure`. In the UI, assert
    `#bidChart` becomes visible and renders an ECharts canvas/svg.
15. Screenshot as `playwright/screenshots/bi-e2e-3-chart.png`.

## Scenario 4 — Save as dashboard + reload persistence

16. Click `#bidSaveBtn` (or `POST /bi_dashboard/api/dashboards` with body
    `{"title":"E2E Sales","tiles":[{"spec":<spec>,"w":12}]}`). Assert HTTP 201
    and a non-empty `id`; capture `dashboard_id`.
17. Assert the dashboard is listed for its owner: `GET /bi_dashboard/api/dashboards`
    includes `dashboard_id`, and it appears in `#bidDashboardList` after reload.
18. Navigate to `http://127.0.0.1:<PORT>/bi_dashboard/<dashboard_id>`; assert
    HTTP 200 and the saved tile renders (the chart survives the reload).
19. Screenshot as `playwright/screenshots/bi-e2e-4-saved-dashboard.png`.

## Scenario 5 — Owner isolation (cnr-bi-01)

Requires a second dashboard user (different `user_id`, same or different tenant).
When only one API key is available, verify the contract at the data layer per the
note below instead of skipping.

20. As a **different** user, `GET /bi_dashboard/api/dashboards/<dashboard_id>`
    (the dashboard owned by the Scenario-4 user). Assert HTTP **404** — a
    non-owner must not read another user's dashboard.
21. As that different user, `PUT` and `DELETE` the same `dashboard_id`. Assert
    HTTP **403** (same tenant) or **404** (different tenant); then, as the owner,
    re-GET and assert the dashboard is unchanged / still present.
22. Assert list scoping: the second user's `GET /bi_dashboard/api/dashboards`
    does NOT include `dashboard_id`.
23. Screenshot the owner's list (showing only their own dashboards) as
    `playwright/screenshots/bi-e2e-5-owner-isolation.png`.

> **Single-key fallback:** if a second dashboard user cannot be provisioned, the
> owner/IDOR contract is proven by the route-level pytest suite
> `tests/bi_dashboard/test_routes_auth.py` (cross-owner 404/403, tenant
> isolation, upload allowlist). Cite that run as Scenario-5 evidence and note the
> environment limitation — do NOT weaken this scenario to a vacuous check.

## Expected Results

- `/bi_dashboard` loads with HTTP 200 (gated on `ICDEV_BI_DASHBOARD_ENABLED`),
  shows the CUI banner and the upload/prompt/save controls.
- A valid CSV upload succeeds (200) and a disallowed extension is rejected (400,
  cnr-bi-02).
- A prompt generates a chart spec (200) and rendering shows an ECharts chart.
- Saving returns 201; the dashboard is listed for its owner and survives a
  reload at `/bi_dashboard/<id>`.
- A non-owner gets 404 on read and 403/404 on mutate; list results are
  owner-scoped (cnr-bi-01).
- No JavaScript console errors across the session.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot.

## CUI Verification

- The `/bi_dashboard` studio view displays "CUI // SP-CTI".

## Screenshots

- `playwright/screenshots/bi-e2e-1-studio.png`
- `playwright/screenshots/bi-e2e-2-upload.png`
- `playwright/screenshots/bi-e2e-3-chart.png`
- `playwright/screenshots/bi-e2e-4-saved-dashboard.png`
- `playwright/screenshots/bi-e2e-5-owner-isolation.png`
