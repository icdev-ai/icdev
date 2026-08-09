# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-08-08 (task-e2e-9dd4e15a)

Full-suite run against all menus, use cases, canvases, and chat right panels.

## How it was run

Two things had to be corrected before the numbers meant anything, and both are
worth knowing before the next run:

1. **`.env` exports `ICDEV_DASHBOARD_URL=http://host.docker.internal:5050`.**
   `playwright.config.ts` reads that variable *before* falling back to
   `ICDEV_DASHBOARD_PORT`, so setting only the port does nothing — the first
   attempt silently attached to the shared checkout's dashboard on the canonical
   `icdev` database. Override `ICDEV_DASHBOARD_URL` explicitly, not just the port.
2. **Port 5090 (the port named in the config's own isolation example) was already
   occupied** by another session's dashboard. Probe before choosing one.

The run that produced these numbers:

```bash
ICDEV_DASHBOARD_PORT=5177 \
ICDEV_DASHBOARD_URL=http://127.0.0.1:5177 \
ICDEV_DASHBOARD_HOST=127.0.0.1 \
ICDEV_PG_DATABASE=icdev_e2e ICDEV_PG_DB=icdev_e2e \
ICDEV_PW_RUN_TAG=e2e9dd4 \
  npx playwright test tests/e2e/ --project=chromium --reporter=line
```

`icdev_e2e` (1771 tables, 287 migrations) is used deliberately: the suite writes
kanban fixtures, and on the canonical `icdev` board the live scheduler dispatches
them.

## Route smoke (gate, step 1)

`python tools/testing/route_smoke.py --all` — **PASS, 89/89** (79 nav routes +
10 API endpoints). No broken pages, so the full suite ran.

## Results

| Outcome | Count |
|---|---|
| Passed | 792 |
| Failed | 9 |
| Skipped | 24 |
| Did not run | 12 |
| **Total** | **837** (65 files) |

Wall clock 21.9m. Exit code 1.

The 12 "did not run" are cascade, not additional defects: they follow a failure
inside a `describe.serial` block (mostly NOC canvas and WFC lifecycle).

## Failures

| # | Spec | Failure | Status |
|---|---|---|---|
| 1 | `canvases_extended.spec.ts:105` | `/quality/canvas/new` returned 500 | **FIXED** — see below |
| 2 | `knowledge_search.spec.ts:174` | breadcrumb URL assertion | **FIXED** — test bug, see below |
| 3 | `coworker_lifecycle.spec.ts:167` | `GET /coworker/<id>` served JSON, no CUI banner | Open |
| 4 | `govcon_pipeline.spec.ts:30` | `POST /api/govcon/sam/scan` exceeded the 10s action timeout | Open (external SAM call) |
| 5 | `noc_canvas.spec.ts:67` | `POST /api/noc/alarms` → 403, expected 201 | Open |
| 6 | `noc_canvas.spec.ts:87` | `POST /api/noc/alarms` (empty body) → 403, expected 400 | Open |
| 7 | `noc_canvas.spec.ts:96` | `POST /api/noc/incidents` → 403, expected 201 | Open |
| 8 | `noc_canvas.spec.ts:120` | `POST /api/noc/rfcs` → 403, expected 201 | Open |
| 9 | `wfc_lifecycle.spec.ts:135` | `formId` undefined | Open (cascade of a `beforeAll` failure) |

### Fixed: `/quality/canvas/new` 500 (product defect)

`user` is a reserved word in PostgreSQL. `tools/qdc_canvas/db/init_db.py` quotes
it in the `qdc_audit` DDL; the `_audit()` INSERT in
`tools/qdc_canvas/blueprint.py` did not. Every audit write from that route raised
`SyntaxError: syntax error at or near "user"`, and because `_audit` is called on
the create path, the whole route 500'd — on the primary backend only, which is
why it survived.

Confirmed directly against PostgreSQL before and after quoting the identifier,
then re-verified through the route: `-g "Quality New Design"` → 2 passed.
Fixed in both `tools/` and the `icdev/tools/` mirror.

The same reserved-word failure appears in the dashboard's boot log for
`mc_audit` (`CREATE TABLE ... user TEXT DEFAULT ''` is skipped outright, so the
table never exists). That one is *not* fixed here — it needs a migration, not an
identifier quote.

### Fixed: breadcrumb assertion (test defect)

`knowledge_search.spec.ts` asserted `page.url()` matched `/localhost:\d+\/?$/`.
The config's own documented isolation recipe serves the suite from `127.0.0.1`,
so the assertion failed on a redirect that had actually worked. Now asserts the
*path* is `/`, which is what the test means. It was the only host-pinned URL
assertion in `tests/e2e/`.

### Not fixed

The NOC 403s (5–8) are a single cause — mutating `/api/noc/*` POSTs are rejected
before reaching the handler — and want one triage, not four. #3 and #9 each need
their own. #4 is an external SAM.gov call against a 10s `actionTimeout` and may
not be a defect at all.

## Artifacts

- Screenshots: `.tmp/test_runs/screenshots/`
- Failure screenshots + video: `.tmp/test_runs/playwright-artifacts-e2e9dd4/`
- Captured to the structured build log via `capture_playwright()`.
- No `playwright-results-e2e9dd4.json` / HTML report was written — the JSON and
  HTML reporters do not flush when the run ends with tests in "did not run".
  Use the line output (`.tmp/pw_out.txt`) for this run.

# CUI // SP-CTI
