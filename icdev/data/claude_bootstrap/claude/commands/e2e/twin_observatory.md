# E2E Test: Twin Observatory (twx-obs-01)

Validate the cross-canvas Twin Observatory page renders and its IQE surface answers.

## Preconditions
- Dashboard running (`python tools/dashboard/app.py` or the `/start` flow).
- `ICDEV_TWIN_OBSERVATORY_ENABLED=true` in `.env` (component default is off).
- Authenticated session (the page redirects to `/login` when unauthenticated —
  log in first, like every canvas page).

## Scenario 1 — Page loads
1. Navigate to `http://127.0.0.1:<PORT>/twin-observatory/`.
2. Assert HTTP 200 (not 401/404/500); no Python traceback text in the body.
3. Assert the heading contains **"Twin Observatory"**.
4. Assert the **"CUI // SP-CTI"** banner is visible.
5. Assert the summary tiles show **"REGISTERED TWINS"** with a numeric count.
6. Assert the **"Twin Health Grid"** table (`#twin-grid`) is present with ≥1 row
   (all 11+ registered twins).
7. Assert the **"Cross-Canvas Twin Event Stream"** table (`#twin-events`) is present.
8. Screenshot `playwright/screenshots/twin-obs-e2e-1-dashboard.png`.
9. Assert no JavaScript console errors on load.

## Scenario 2 — Grid content
1. In `#twin-grid`, assert each row has a verdict badge (pass/warn/fail/unknown)
   and a "Last snapshot" cell.
2. Assert at least one row exposes an "open →" click-through link to its canvas
   page (e.g. `/network`, `/pipeline`).

## Scenario 3 — IQE widget
1. Assert the IQE widget ("Ask Twin Observatory") is present and does NOT show
   "IQE endpoint not configured".
2. `POST /twin-observatory/api/iqe-query` with
   `{"question": "list all twins", "execute": true}`.
3. Expect HTTP 200 with a JSON body containing `iqe`, `results`, and `row_count`.
4. Screenshot `playwright/screenshots/twin-obs-e2e-3-iqe.png`.

## Notes
- Backing data is read-only from `tools.twin_core.observer.observe()` +
  `tools.twin_core.event_bridge.recent_twin_events()`; no writes.
- Do NOT set Playwright `outputDir` to `playwright/screenshots/` (it is wiped each
  run) — point it at an `artifacts/` subdir; screenshots are saved explicitly.
