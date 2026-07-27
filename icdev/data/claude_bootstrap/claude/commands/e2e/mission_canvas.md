# E2E Test: Mission Control Canvas

Verify the **Mission Control** canvas at `/mission-canvas/` renders real canvas
data, drives its 13 capabilities through in-page result panels (no `prompt()` /
`alert()` pop-ups), answers inline IQE queries against the `mission.*`
collections, and surfaces citation grounding on generated narratives.

Mission Control is the program command center — a unified view over the Mission
Canvas tables (`mission_designs`, `mission_twin_snapshots`, `mission_evidence`,
`mission_security_posture`, ...). This spec drives the real page in a browser and
asserts the cnr-mc-01..03 fixes: correct DB wiring, `mission.*` IQE resolution,
real result panels, and grounded narrative output.

## Prerequisites
- Flask dashboard running on `http://localhost:5050`
- `ICDEV_MISSION_CANVAS_ENABLED=true` in `.env`
- Mission Canvas DB initialized (`python -m tools.mission_canvas.db.init_db`) with
  at least one `mission_designs` row seeded so the missions table + IQE return data
- A valid dashboard API key for login

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/mission_canvas-<scenario>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (`page.content()`) and a screenshot named
> `playwright/screenshots/mission_canvas-<scenario>-FAIL.png` BEFORE aborting.

## Scenario 1 — Load /mission-canvas/ (page renders + real data)

### Login
1. Navigate to `http://localhost:5050/login`
2. Fill the API key field with the test API key
3. Click "Login" and assert redirect to the home page (`/`)

### Nav link + page render
4. On the home page, assert a nav link whose text contains "Mission Control"
   resolves to `/mission-canvas/`
5. Navigate to `http://localhost:5050/mission-canvas/`
6. Assert HTTP 200 (NOT 404) and no traceback text is present
7. Assert the main heading contains "Mission Control"
8. Assert the CUI banner "CUI // SP-CTI" is visible at top and bottom
9. Assert the **Missions** section renders a table row for each seeded
   `mission_designs` row (name + classification visible) — proves the index reads
   the real canvas DB, not the removed `mission_canvas_sessions` table
10. Assert the hero stat "Missions" shows a count >= 1
11. Screenshot as `playwright/screenshots/mission_canvas-load.png`
12. Check the browser console — assert no JavaScript errors on load

## Scenario 2 — Capabilities render into panels (no pop-ups)

13. Assert the Mission ID input (`#mc-mission-id`) is visible and pre-filled with a
    real mission id
14. Assert NO `window.prompt(` and NO `alert(JSON.stringify(` occur in the page
    source (demo-grade UI removed)
15. Assert there is NO capability button wired to `runApi('drift')` (the dead
    Drift button was removed)
16. Register a dialog handler that FAILS the test if any native dialog fires
    (prompt/alert must never appear)
17. Click "Twin Snapshot" (Situation zone) and assert the result panel
    (`#mc-result`) becomes visible with a status of the form "HTTP <code>" and a
    JSON body in `#mc-result-body`
18. Click "Posture" (Security zone) and assert the panel updates to the Security
    Posture capability result
19. Screenshot as `playwright/screenshots/mission_canvas-capability-panel.png`

## Scenario 3 — Inline IQE query (mission.* collections)

20. Assert the IQE query mini-bar (shared `includes/iqe_query_widget.html`) is
    visible with example chips
21. Enter the query "List operational missions" and run it
22. Assert the widget shows a translated IQE string referencing `mission.sessions`
    and a results area with >= 1 row (the seeded design), with no error banner
23. Screenshot as `playwright/screenshots/mission_canvas-iqe.png`

## Scenario 4 — Narrative grounding surfaced

24. POST to `/mission-canvas/api/narrative` with a `mission_id`, `topic`, and a
    `sources` list of one evidence item
25. Assert the JSON response contains a `grounding` object with a `report`
    (`cited_count`, `hallucinated_citations`, `citation_rate`) and a `grounded`
    boolean — proving `tools/quality/citation_grounding.py` is wired to the LLM
    executive summary (uncited/hallucinated citations are flagged, not silently
    accepted)

## Acceptance
- All four scenarios pass; screenshots captured.
- The index reads the real Mission Canvas DB (Scenario 1).
- Capabilities render into `#mc-result` with zero native dialogs (Scenario 2).
- IQE resolves `mission.*` collections against real tables (Scenario 3).
- Narrative output carries a grounding report (Scenario 4).
