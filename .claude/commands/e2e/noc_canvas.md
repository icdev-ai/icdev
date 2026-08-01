# E2E Test: NOC Operations Canvas (NOCC)

Verify the **NOC Operations Canvas** at `/noc` renders its pages and honors its
hardened contracts: the alarm/incident/RFC/MOP mutation lifecycles, the
cnr-ops-01 fail-closed auth gate on mutating routes, the cnr-ops-02 TTL-cached
adapter probes, and the validated looking-glass embed.

NOCC lets a NOC operator triage alarms, run the incident lifecycle, plan RFCs /
MOPs / maintenance windows, and watch SLA burn. This spec drives the real pages
and asserts these regressions stay fixed:

- **cnr-ops-01** — mutating `/api/noc/*` routes fail closed: an unauthenticated
  POST/PUT returns 401 regardless of `ICDEV_ENFORCE_CANVAS_ACCESS`.
- **cnr-ops-02** — `/noc/looking-glass` never embeds a non-`http(s)` URL; an
  empty/invalid `HYPERGLASS_URL` renders the friendly ad-hoc-lookup state, not a
  broken/hostile iframe.

The executable spec is `tests/e2e/noc_canvas.spec.ts` (Playwright).

## Prerequisites

- Flask dashboard running at `<PORT>` (default `5050`, `ICDEV_DASHBOARD_PORT`).
- `ICDEV_NOCC_ENABLED=true` (canvas toggle) in `.env`; when unset `/noc` 404s.
- Database initialized; NOCC tables (`noc_alarms`, `noc_incidents`, `noc_rfcs`,
  `noc_mops`, `noc_maintenance_windows`) present (created lazily on first request).
- Logged in — `ICDEV_DASHBOARD_API_KEY` auto-login on `/login`, or a manual key.
  The login session satisfies the cnr-ops-01 mutation gate; the unauthenticated
  scenarios below use a request context that presents no session/key.

> **Screenshots (repo rule):** save to `playwright/screenshots/noc-e2e-<n>.png`.
> **Failure handling:** on any failed assertion capture a DOM snapshot + a
> `-FAIL` screenshot before aborting (a 401 on an authed POST proves the session
> did not persist; a 404 proves the canvas is not wired).

## Scenario 1 — Pages render (overview + sub-pages, CUI banners)

1. Navigate to `/noc`; assert HTTP 200 (not 404), the "NOC" heading, and the CUI
   banner "CUI // SP-CTI". Screenshot `noc-e2e-01-load-overview.png`.
2. Repeat for `/noc/alarms`, `/noc/incidents`, `/noc/rfcs`, `/noc/mops`,
   `/noc/maintenance`, `/noc/sla` — each 200 with the CUI banner.
3. `GET /api/noc/overview`, `/api/noc/alarms`, `/api/noc/sla` return JSON 200.

## Scenario 2 — Alarm lifecycle (ingest → ack → clear)

4. `POST /api/noc/alarms` `{alarm_source, description, severity}` → 201 + `id`.
5. `POST /api/noc/alarms/<id>/ack` → 200 `{success:true}`.
6. `POST /api/noc/alarms/<id>/clear` → 200. Reload `/noc/alarms`; screenshot.
7. `POST /api/noc/alarms` with an empty body → **400** (validation), proving the
   gate passed for the authed caller (not 401).

## Scenario 3 — Incident lifecycle (create → update)

8. `POST /api/noc/incidents` `{title, severity}` → 201 + `incident_number`
   matching `^INC-`.
9. `PUT /api/noc/incidents/<id>` `{status:"investigating"}` → 200.

## Scenario 4 — RFC + MOP

10. `POST /api/noc/rfcs` `{title, change_type, risk_level}` → 201 + `rfc_number`
    matching `^RFC-`.
11. `POST /api/noc/mops/generate` `{rfc_id, title, context}` → 201 with `mop_id`
    and a non-empty `steps[]`.

## Scenario 5 — Fail-closed auth (cnr-ops-01)

12. With NO session/API key, `POST /api/noc/incidents`, `PUT /api/noc/incidents/x`,
    `POST /api/noc/rfcs`, `POST /api/noc/mops/generate`, `POST /api/noc/maintenance`
    each return **401**. (The route-level pytest `tests/test_cnr_ops_mutation_auth.py`
    also proves this deterministically.)

## Scenario 6 — Looking-glass hardening (cnr-ops-02)

13. Navigate to `/noc/looking-glass`; assert 200 and "Looking Glass" heading.
14. Assert no `<iframe src>` begins with `javascript:` or `data:`. With
    `HYPERGLASS_URL` unset/invalid, the ad-hoc-lookup panel renders; with a valid
    `https://…` URL, the hyperglass iframe embeds that exact URL.

## Expected Results

- All NOCC pages load 200 with CUI banners; read APIs return JSON.
- Alarm/incident/RFC/MOP mutation flows succeed for the authed operator.
- Unauthenticated mutations return 401 (cnr-ops-01).
- Looking-glass never embeds a non-http(s) URL (cnr-ops-02).
- No JavaScript console errors across the session.

## Screenshots

- `playwright/screenshots/noc-e2e-01-load-overview.png`
- `playwright/screenshots/noc-e2e-03-alarm-lifecycle.png`
- `playwright/screenshots/noc-e2e-04-incident-lifecycle.png`
- `playwright/screenshots/noc-e2e-05-rfc-mop.png`
- `playwright/screenshots/noc-e2e-06-looking-glass.png`
