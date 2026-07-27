# E2E Test: Migration Design Canvas (MDC)

Verify the **Migration Design Canvas** at `/migration-canvas` renders and honors
the contracts hardened in the CNR production-readiness pass: a paginated design
index, an authenticated dossier-guidance API that returns real research-backed
guidance (not a silent empty list), an AI assistant whose responses carry a
grounding verdict, and the server/network migration wizards.

MDC lets an operator design 7Rs modernization workflows, plan server (P2V/cloud)
and network-device migrations, and surface research-dossier guidance per wizard
step. This spec drives the real pages in a browser and asserts these regressions
stay fixed:

- **cnr-mdc-01** — `GET /migration-canvas/api/server-migration/guidance/<step>`
  returns non-empty, research-backed guidance for a seeded step (was silently `[]`
  on PostgreSQL because of mixed `?`/`%s` placeholders swallowed by a bare except).
- **cnr-mdc-02** — that guidance route now requires auth (`@mdc_login_required`),
  and the canvas passes its 8-point completeness gate (registry `db_migration`
  points at the real `tools/migration_canvas/db/init_db.py`).
- **cnr-mdc-03** — the design index is paginated/bounded (`?page=&per_page=`), and
  the network AI assistant attaches a `grounding` verdict (citations or a
  grounding-warning flag) to every LLM-drafted response.

## Prerequisites

- Flask dashboard running at the configured port. The default is `5050`
  (`ICDEV_DASHBOARD_PORT`); if another session already owns `5050`, start your own
  instance on a free port with `python tools/dashboard/app.py --port 5099` and
  substitute that port everywhere below. This spec is written port-agnostic — read
  `<PORT>` as the port your dashboard is bound to.
- `ICDEV_MIGRATION_CANVAS_ENABLED=true` in `.env` — the canvas is gated behind this
  toggle; when unset the nav link is hidden and `/migration-canvas` 404s.
- Database initialized. MDC canvas tables use `MC_STORAGE_BACKEND`
  (PostgreSQL-primary, falling back to a dedicated SQLite file at
  `data/migration_canvas.db`). The initializer
  `tools/migration_canvas/db/init_db.py` creates `migration_designs`, `mc_*`, and
  the network/server migration tables.
- A valid dashboard API key. When `ICDEV_DASHBOARD_API_KEY` is set in `.env` the
  `/login` page auto-authenticates and redirects home; otherwise log in with the
  key on `/login` first.
- **Do NOT set `ICDEV_AUTH_BYPASS`** for this spec — Scenario 3 asserts the
  unauthenticated 401 contract, which the bypass would defeat. (The blueprint now
  logs a SECURITY warning at construction when `ICDEV_AUTH_BYPASS` is set with no
  CI/E2E marker present.)

### Seeding (so a fresh runner can pass every scenario)

- **Designs (Scenarios 1–2):** the index lists designs from `migration_designs`.
  If empty, create one from the UI (`+ New` on `/migration-canvas`, or
  `/migration-canvas/canvas/new`), or over the API:

  ```bash
  curl -s -X POST http://127.0.0.1:<PORT>/migration-canvas/api/designs \
    -H 'Content-Type: application/json' \
    -d '{"name":"E2E Migration Design","migration_type":"application"}'
  ```

- **Dossier guidance (Scenario 4):** the advisor reads scored `research_challenges`
  for its target session (`rsess-1e2fb0fe6c96`). On a fresh DB with no research
  data, the guidance API legitimately returns an empty `items[]` — in that case
  assert the **contract** (HTTP 200, `{"ok": true, "items": [...]}`, no 500 and no
  traceback) rather than a non-empty list, and note that a seeded research session
  is required to exercise the non-empty path. The regression being guarded is that
  the call must not silently swallow a backend error into `[]`.

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/mdc-e2e-<n>-<slug>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (accessibility tree / `page.content()`) and a screenshot named
> `playwright/screenshots/mdc-e2e-<n>-<slug>-FAIL.png` BEFORE aborting, so the
> failure is actionable (a 404 body proves the canvas route is not wired; a 500
> body proves a schema/placeholder mismatch rather than a UI regression).

## Scenario 1 — /migration-canvas dashboard renders (design list + CUI banner)

### Login
1. Navigate to `http://127.0.0.1:<PORT>/login`. With `ICDEV_DASHBOARD_API_KEY` set,
   assert the redirect to home (`/`); otherwise fill the API key field, click
   "Login", and assert the redirect.

### Dashboard render
2. Navigate to `http://127.0.0.1:<PORT>/migration-canvas`
3. Assert the response is HTTP 200 (NOT 404) and no traceback text is present.
4. Assert the page heading contains "Migration Design Canvas".
5. Assert the CUI banner "CUI // SP-CTI" is visible (rendered by `base.html`).
6. Assert the design list region is present. If empty, seed a design per
   Prerequisites and reload; assert at least one design card/link whose `href`
   matches `/migration-canvas/canvas/<id>` (excluding the `+ New` /
   `/migration-canvas/canvas/new` create link).
7. Screenshot the dashboard as `playwright/screenshots/mdc-e2e-1-dashboard.png`.
8. Check the browser console — assert no JavaScript errors on load.

## Scenario 2 — Design index is paginated/bounded (cnr-mdc-03)

9. Request the index with an explicit small page size:
   `http://127.0.0.1:<PORT>/migration-canvas?page=1&per_page=1`. Assert HTTP 200.
10. Assert the route honors `per_page` — at most one design card is rendered in the
    designs list even when more than one design exists (seed a second design first
    if needed to make the bound observable).
11. Assert an out-of-range `per_page` is clamped, not trusted: request
    `?per_page=100000` and assert the page still returns HTTP 200 without loading an
    unbounded list (the server caps `per_page` at 200). A non-numeric `?page=abc`
    must also return HTTP 200 (falls back to page 1), never a 500.
12. Screenshot the paginated index as `playwright/screenshots/mdc-e2e-2-pagination.png`.

## Scenario 3 — Guidance API requires authentication (cnr-mdc-02)

13. In a context WITHOUT a session cookie / API key (a fresh client, or after
    clearing auth), call
    `GET http://127.0.0.1:<PORT>/migration-canvas/api/server-migration/guidance/1`.
    Assert HTTP 401 with a JSON body `{"error": "Authentication required"}` — the
    route is decorated `@mdc_login_required` and must NOT serve guidance
    unauthenticated. (Regression: this was the only MDC route missing the
    decorator.)
14. Capture the 401 response body as evidence
    (`playwright/screenshots/mdc-e2e-3-guidance-401.png` or the raw JSON).

## Scenario 4 — Dossier guidance returns research-backed items, never a silent [] (cnr-mdc-01)

15. Authenticated, call
    `GET http://127.0.0.1:<PORT>/migration-canvas/api/server-migration/guidance/1`.
    Assert HTTP 200 and a JSON body shaped `{"ok": true, "items": [...]}` — assert
    NO HTTP 500 and no traceback text (the advisor must not swallow a backend error
    into an empty list).
16. When a research session is seeded (scored `research_challenges` for the target
    session), assert `items[]` is non-empty and each item carries `title`,
    `severity`, and `category`. On a fresh DB with no research data, assert the
    contract from step 15 and record that the non-empty path needs a seeded session.
17. Repeat for another mapped step (e.g. `/guidance/5`) and assert the same
    contract holds (HTTP 200, well-formed `items[]`).
18. Screenshot / capture the guidance response as
    `playwright/screenshots/mdc-e2e-4-guidance.png`.

## Scenario 5 — Network migration AI assistant attaches a grounding verdict (cnr-mdc-03)

19. Open a network migration session: navigate to
    `http://127.0.0.1:<PORT>/migration-canvas/network-migration/new`, complete the
    minimal source/target model fields, and capture the resulting `<session_id>`
    (or seed one via the network-migration API).
20. Invoke the AI assistant for that session:
    `POST /migration-canvas/api/network-migration/<session_id>/ai-assist` with body
    `{"prompt":"How should I migrate BGP peers to the target device?"}`. Assert
    HTTP 200.
21. Assert the response carries a `grounding` object. Because this is free-form LLM
    guidance with no retrieval context, assert the grounding verdict reflects that
    honestly: `grounding.has_citations` is a boolean, `grounding.confidence_band`
    is one of `include` / `flag` / `abstain`, and when no `[source: …]` citations
    are present, `grounding.grounding_warning` is a non-empty string (the
    model-generated-guidance warning). This proves the TRUST wiring is live even
    when the LLM is unavailable (the fallback text is also assessed).
22. Screenshot / capture the AI-assist response as
    `playwright/screenshots/mdc-e2e-5-ai-grounding.png`.
23. Check the browser console — assert no JavaScript errors accumulated across the
    session.

## Expected Results

- `/migration-canvas` loads with HTTP 200 (gated on
  `ICDEV_MIGRATION_CANVAS_ENABLED=true`), shows the CUI banner, and lists designs.
- The design index honors `?page=&per_page=`, clamps `per_page` to a sane maximum,
  and tolerates non-numeric params without erroring (cnr-mdc-03).
- The guidance API returns 401 unauthenticated (cnr-mdc-02) and, authenticated,
  returns a well-formed `{"ok": true, "items": [...]}` — never a 500 swallowed into
  `[]` (cnr-mdc-01).
- The network AI assistant attaches a `grounding` verdict with a warning flag when
  responses are uncited (cnr-mdc-03 / TRUST invariant).
- No JavaScript console errors across the session.
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot.

## CUI Verification

- The `/migration-canvas` dashboard (Scenario 1) displays "CUI // SP-CTI" via the
  shared `base.html` banner chrome.

## Screenshots

- `playwright/screenshots/mdc-e2e-1-dashboard.png`
- `playwright/screenshots/mdc-e2e-2-pagination.png`
- `playwright/screenshots/mdc-e2e-3-guidance-401.png`
- `playwright/screenshots/mdc-e2e-4-guidance.png`
- `playwright/screenshots/mdc-e2e-5-ai-grounding.png`
