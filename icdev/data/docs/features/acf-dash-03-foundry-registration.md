# ACF Foundry — Dashboard Registration, Nav Link & Pages (acf-dash-03)

CUI // SP-CTI

## Summary

Wires the Autonomous Capability Foundry (ACF) canvas into the dashboard so the
`/foundry` page is reachable once the `ICDEV_FOUNDRY_ENABLED` flag is set. The
blueprint, templates, and routes were authored by `acf-dash-02`; this task makes
them discoverable and registers them.

## Changes

1. **Blueprint registration** — `tools/dashboard/app.py`
   - Added `("foundry", "ICDEV_FOUNDRY_ENABLED", "tools.foundry.blueprint", "create_foundry_blueprint")`
     to `_CANVAS_DEFS`. Default **OFF** (`foundry` is not in `_CANVAS_DEFAULTS_TRUE`),
     so the canvas is fully dark unless the flag is set.
   - Added `"foundry": ""` to the `_CANVAS_ROUTES` map. The blueprint defines its
     own absolute paths (`/foundry`, `/foundry/<id>`, `/api/foundry/*`) — same
     pattern as `integrity`/`logs`. Without the empty-prefix entry the loop
     defaulted to a `/foundry` url_prefix and produced the doubled path
     `/foundry/foundry`. Fixed.

2. **Nav link** — `tools/dashboard/templates/base.html`
   - Added `Foundry (ACF)` link under the **Platforms ▾ → Market & AI** group,
     beside Genesis/Oracle, pointing to `/foundry`.
   - Extended the Platforms dropdown active-state test with
     `request.path.startswith('/foundry')` so the menu highlights on foundry pages.

3. **Route registry** — `.claude/commands/start.md`
   - Added `/foundry` and `/foundry/<id>` to the dashboard `Pages:` line so the
     kanban route verifier recognizes the routes.

## Verification

- App boots with `ICDEV_FOUNDRY_ENABLED=true` — **no ImportError**.
- `url_map` exposes `/foundry`, `/foundry/`, `/foundry/<concept_id>`, and the
  `/api/foundry/*` JSON endpoints (no doubled prefix).
- Unauthenticated `GET /foundry` → `302 → /login` (auth-gate parity with the
  known-good `/integrity` sibling).
- Authenticated `GET /foundry` → **200**, full page render (≈74 KB).
- Authenticated `GET /foundry/<unknown-id>` → **404** JSON `concept not found`
  (intentional — empty concept set in a fresh DB).

## Notes

- The `icdev/` mirror of `app.py` is synced separately by `companion.py` (it lags
  the canonical tree — e.g. it does not yet carry `logs`), so canvas registration
  is hand-edited only in `tools/dashboard/app.py`. Foundry **templates** were
  mirrored to `icdev/tools/dashboard/templates/foundry/` per the new-page gate.
- Depends on `acf-dash-02` (blueprint + templates + routes), committed here to
  keep the branch self-consistent.
