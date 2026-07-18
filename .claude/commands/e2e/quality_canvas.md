# E2E Test: Quality Design Canvas (QDC)

Verify the **Quality Design Canvas** at `/quality` renders and honors the
contracts hardened by the `cnr-qdc-*` production-readiness work: authentication
on every route, XSS-safe rendering of attacker-controlled graph JSON, a wired
IQE query widget, graceful rendering on a fresh database, and collaboration
operations that actually persist and replay.

QDC lets an operator design NIST SA-11 quality-gate topologies, compute the
Unified Quality Score (UQS), map gates to SA-11 enhancements, and emit OSCAL
evidence. This spec drives the real pages in a browser and asserts the following
regressions stay fixed:

- **cnr-qdc-01** — every `/quality` route is authenticated. Unauthenticated page
  GETs redirect to `/login`; API routes and mutating methods (incl. the
  destructive `DELETE /quality/api/designs`) return JSON `401`.
- **cnr-qdc-02** — a design's `graph_json` (attacker-controlled via
  `PUT /quality/api/designs/<id>`) is rendered as an HTML-safe JSON data island,
  so a `</script><script>alert(1)</script>` payload cannot break out.
- **cnr-qdc-03** — the IQE widget on `/quality` is wired to
  `POST /quality/api/iqe-query` and answers a seed query instead of showing
  "IQE endpoint not configured".
- **cnr-qdc-04** — `/quality` and its sub-pages render (HTTP 200, no 500) even on
  a fresh DB with tables absent; list queries are bounded (LIMIT + `?page`).
- **cnr-qdc-05** — collab `push` persists the operation and `poll?since=` replays
  it; the assessment response no longer carries the dead `chain_mode` field.

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session already owns `5050`, start your
  own instance on a free port with `python tools/dashboard/app.py --port 5099`
  and substitute that port everywhere below. This spec is written port-agnostic —
  read `<PORT>` as the port your dashboard is bound to.
- `ICDEV_QDC_ENABLED=true` in `.env` — the canvas is gated behind this toggle;
  when unset the nav link is hidden and `/quality` 404s.
- Database initialized. QDC uses `QDC_STORAGE_BACKEND` (falls back to
  `ICDEV_CANVAS_STORAGE_BACKEND` / `ICDEV_STORAGE_BACKEND`; default PostgreSQL).
  The blueprint calls `init_db()` at import, creating and seeding the `qdc_*`
  tables (`qdc_designs`, `qdc_templates`, `qdc_snippets`, `qdc_runbooks`,
  `qdc_sops`, `qdc_collab_sessions`, `qdc_collab_ops`).
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates and redirects home; otherwise log in with the
  key on `/login` first. For CI parity, `ICDEV_AUTH_BYPASS=1` authenticates all
  `/quality` routes without a session.

### Seeding (so a fresh runner can pass every scenario)

- **Designs (Scenarios 2–5):** `/quality` lists designs from `qdc_designs`. If
  empty, create one with `+ New Design` on `/quality`, from a template via
  `/quality/templates` → *Use*, or over the API. To exercise the XSS assertion
  (Scenario 3), seed a design whose graph carries a script-breakout payload —
  **pass `graph_json` as a JSON object, not a pre-stringified string**:

  ```bash
  # create an empty design, then PUT a malicious graph_json
  DID=$(curl -s -X POST http://127.0.0.1:<PORT>/quality/api/designs \
    -H 'Content-Type: application/json' -H 'X-ICDEV-API-Key: <KEY>' \
    -d '{"name":"XSS Probe"}' | python -c 'import sys,json;print(json.load(sys.stdin).get("design_id",""))')
  curl -s -X PUT http://127.0.0.1:<PORT>/quality/api/designs/$DID \
    -H 'Content-Type: application/json' -H 'X-ICDEV-API-Key: <KEY>' \
    -d '{"graph_json":{"nodes":[],"edges":[],"x":"</script><script>alert(1)</script>"}}'
  ```

  (If `/quality` has no create-design API in your build, open `/quality/canvas/new`
  in the UI to create one, then PUT the malicious `graph_json` to its id.)

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/qdc-e2e-<n>-<slug>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (`page.content()`) and a screenshot named
> `playwright/screenshots/qdc-e2e-<n>-<slug>-FAIL.png` BEFORE aborting (a 401 body
> proves an auth gap, a 500 body proves a schema/table mismatch rather than a UI
> regression).

## Scenario 1 — Auth is enforced on every route (cnr-qdc-01)

Run these BEFORE logging in (no session, `ICDEV_AUTH_BYPASS` unset).

1. `DELETE http://127.0.0.1:<PORT>/quality/api/designs` (delete-all). Assert HTTP
   `401` and body `{"error":"Authentication required"}` — the design table must be
   untouched.
2. `PUT http://127.0.0.1:<PORT>/quality/api/designs/x` with `{"name":"y"}`. Assert
   HTTP `401`.
3. `GET http://127.0.0.1:<PORT>/quality/` in the browser. Assert a redirect to
   `/login` (HTTP 302 → login page), NOT a 200 render.
4. Screenshot the `/login` redirect as `playwright/screenshots/qdc-e2e-1-auth.png`.

## Scenario 2 — /quality dashboard renders (design list + CUI banner) (cnr-qdc-04)

### Login
5. Navigate to `http://127.0.0.1:<PORT>/login`. With `ICDEV_DASHBOARD_API_KEY`
   set, assert the redirect to home; otherwise fill the API key field and log in.

### Dashboard render
6. Navigate to `http://127.0.0.1:<PORT>/quality`.
7. Assert HTTP 200 (NOT 401/404) and no traceback text is present.
8. Assert the heading contains "Quality Design Canvas".
9. Assert the "CUI // SP-CTI" banner is visible.
10. Assert the Quick Start Templates section lists the seeded templates (e.g.
    "FedRAMP Moderate QA", "CMMC Level 2 QA").
11. Assert the sub-pages render 200: `/quality/templates`, `/quality/snippets`,
    `/quality/runbooks`, `/quality/sops`, `/quality/assessments`.
12. Screenshot the dashboard as `playwright/screenshots/qdc-e2e-2-dashboard.png`.
13. Check the browser console — assert no JavaScript errors on load.

## Scenario 3 — Stored XSS is escaped (cnr-qdc-02)

14. Ensure an "XSS Probe" design exists whose `graph_json` contains
    `</script><script>alert(1)</script>` (see Seeding).
15. Navigate to `http://127.0.0.1:<PORT>/quality/canvas/<xss-probe-id>`.
16. Assert HTTP 200 and that **no** dialog/alert fires (no `alert(1)`).
17. Inspect `page.content()`: assert the literal `</script><script>alert(1)</script>`
    does NOT appear as executable markup, and the payload appears escaped inside the
    `<script type="application/json" id="qdc-initial-graph">` data island (the
    `<`/`>` rendered as `<` / `>`).
18. Assert the canvas still loads the (empty) graph — status text such as
    "Empty canvas" — proving the data island is parsed, not merely blanked.
19. Screenshot the canvas as `playwright/screenshots/qdc-e2e-3-xss-escaped.png`.

## Scenario 4 — IQE widget answers a seed query (cnr-qdc-03)

20. On `http://127.0.0.1:<PORT>/quality`, locate the IQE widget ("Ask Quality
    Canvas"). Assert it does NOT display "IQE endpoint not configured".
21. Click an example chip (e.g. "All designs") or type "list all designs" and Run.
22. Equivalent API assertion: `POST http://127.0.0.1:<PORT>/quality/api/iqe-query`
    with `{"question":"list all designs","execute":true}`. Assert HTTP 200 and a
    body with `iqe`, `results`, and `row_count` (or a structured `{"error":...}` —
    never an unhandled 500 crash / HTML traceback).
23. Screenshot the widget result as `playwright/screenshots/qdc-e2e-4-iqe.png`.

## Scenario 5 — Collab op persistence + replay; no dead chain_mode (cnr-qdc-05)

24. Join a collab session on a seeded design:
    `POST /quality/api/collab/<id>/join` with `{"user_id":"u1","user_name":"One"}`.
    Capture `session_id`.
25. Push two operations:
    `POST /quality/api/collab/<id>/push` with
    `{"session_id":"<sid>","user_id":"u1","operation":{"type":"add","node":"n1"}}`
    then the same with `node":"n2"`. Assert each returns `{"ok":true,"seq":N}` with
    `seq` incrementing 1, 2.
26. Poll from the start: `GET /quality/api/collab/<id>/poll?since=0`. Assert
    `operations[]` has both ops in order, `cursor == 2`, and the first op's
    `operation.node == "n1"` (proving persistence + replay, not a presence-only
    stub).
27. Poll from the cursor: `GET /quality/api/collab/<id>/poll?since=2`. Assert
    `operations == []` and `cursor == 2`.
28. Run an assessment: `POST /quality/api/designs/<id>/assess`. Assert HTTP 200 and
    that the response body does NOT contain a `chain_mode` field (the dead
    `use_cod`/`chain_mode` params were removed).
29. Screenshot the canvas with any collab UI as
    `playwright/screenshots/qdc-e2e-5-collab.png`.
30. Check the browser console — assert no JavaScript errors accumulated across the
    session.

## Expected Results

- Every `/quality` route is authenticated: unauth page GET → `/login`; unauth API
  / mutating method → JSON `401` (cnr-qdc-01).
- `/quality` and all sub-pages load HTTP 200 with the CUI banner and seeded lists,
  even on a fresh DB (cnr-qdc-04).
- A design whose `graph_json` carries a `</script>` payload renders it escaped in
  the JSON data island — no script executes (cnr-qdc-02).
- The IQE widget is configured and `POST /quality/api/iqe-query` answers a seed
  query with `iqe` / `results` / `row_count` (cnr-qdc-03).
- Collab `push` returns an incrementing `seq`, `poll?since=` replays persisted ops
  and advances `cursor`; the assess response omits `chain_mode` (cnr-qdc-05).
- No JavaScript console errors across the session.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot.

## CUI Verification

- The `/quality` dashboard (Scenario 2) displays "CUI // SP-CTI".
- The full-bleed canvas editor intentionally omits the banner chrome — assert the
  CUI banner only on the dashboard/index view.

## Screenshots

- `playwright/screenshots/qdc-e2e-1-auth.png`
- `playwright/screenshots/qdc-e2e-2-dashboard.png`
- `playwright/screenshots/qdc-e2e-3-xss-escaped.png`
- `playwright/screenshots/qdc-e2e-4-iqe.png`
- `playwright/screenshots/qdc-e2e-5-collab.png`
