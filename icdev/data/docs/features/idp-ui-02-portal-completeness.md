# CUI // SP-CTI

# IDP Portal — the 8-point completeness gate, satisfied by the page that enforces it

**Task:** `idp-ui-02` · **Project:** IDP — Internal Developer Portal

## What shipped

`/idp` — a dashboard page that renders ICDEV's own component catalog and grades
it against `args/scorecards/component-readiness.yaml`. The page is the surface
`idp-score-02` was built to feed: the IQE adapter that supplies the scorecard's
rule language is the same adapter that supplies the catalog's rows.

It grades itself. The portal appears in its own catalog, passes its own 8-point
gate, and reports its own ownership gap rather than exempting itself from the
rule it grades every other component on.

## The 8 points

CLAUDE.md's dashboard-page gate applies in full to a new page, and all eight
had to land together. Verified by `validate_canvas_completeness("idp")`, which
returns `passed=True` with **all eight items present** — not merely passing
because an optional point was skipped.

| # | Point | Artifact |
|---|-------|----------|
| 1 | Page template | `tools/dashboard/templates/idp/page.html` |
| 2 | `icdev/` mirror | `icdev/tools/dashboard/templates/idp/page.html` (byte-identical; pinned by a test) |
| 3 | Route | `@bp.route("/")` in `tools/idp/blueprint.py` |
| 4 | Backing module | `tools/idp/portal.py` (+ the existing `tools/idp/scorecard.py`) |
| 5 | Constants | `tools/idp/constants.py` |
| 6 | DB | `tools/idp/db/init_db.py` — see below |
| 7 | Nav link | `nav.links` in `args/component_registry.yaml` → Canvases menu |
| 8 | IQE | adapter + `POST /idp/api/iqe-query` + widget include + dispatch entry + `PATH_CANVAS` entry + 4 seed queries |

### Point 6 deserves an explanation

The portal needs **no new table**. Everything it renders is derived at request
time from `args/component_registry.yaml`, the repo tree, and
`args/scorecards/*.yaml`. Inventing a table so a checklist item could be ticked
would have created a second DDL source for data nobody writes — exactly the
failure mode the "every column in an INSERT must exist in the LIVE schema" rule
exists to prevent.

What the portal *does* depend on is three tables it does not own:
`developer_scorecards` (persisted scores — `idp-score-01`/`03`),
`awareness_component_health` (route probes), and `kg_edges` (blast radius —
`idp-cat-02`). So point 6 is satisfied by the *graceful-degradation* half of
its own wording — "table existence handled gracefully if migration hasn't run
yet": one catalog query reports which exist, and the page shows an absent
signal as **not measured** rather than as a passing zero. A component that was
never probed must never look the same as one that passed.

That probe uses a single `information_schema` / `sqlite_master` read rather
than one `SELECT 1 FROM <t>` per table, because on PostgreSQL the first miss
aborts the transaction and every *subsequent* table then reports "does not
exist" whether or not it does.

### Points 7 and 8 are derived, not written

Per the CLAUDE.md registry rule, no Python list in `tools/dashboard/app.py`,
`tools/cli/enable.py` or `base.html` was touched. Declaring the component in
`args/component_registry.yaml` produced, with no further edit:

* the blueprint mount at `/idp` (`_CANVAS_BLUEPRINTS` loop),
* the Canvases-menu entry (`get_nav_context()` → `base.html`),
* the `icdev enable idp` toggle (`get_cli_toggles()`),
* the `/api/iqe/dispatch` mapping (`get_iqe_mapping()` → `_IQE_CANVAS_MAP`),
* the client-side `^/idp` → `idp` mini-bar regex (`get_iqe_path_canvas()`).

`tests/test_idp_portal.py::test_registration_is_derived_not_hardcoded` asserts
all five, so a future hardcoded list fails the suite rather than passing
silently.

## Eating the dog food

Measured on the tree at merge time:

| | |
|---|---|
| Components in catalog | 67 (all kinds) |
| Canvases in scope for the 8-point gate | 37 |
| Canvases passing it | 36 → 37 with this page |
| The portal's own level | **Gold** (score 72%) |
| Rules the portal fails | `has-owner`, `on-call-named`, `owner-reachable` |

Gold is the honest ceiling. Platinum requires `has-owner`, and **no component
in ICDEV has an owner** — the repo carries no CODEOWNERS file, no team roster
and no maintainer metadata to backfill from, which is exactly the gap
`idp-cat-01` measured and left unpopulated on purpose.

Naming a placeholder owner for this one component would have made the single
surface whose job is to report that gap the single surface that lies about it.
So the registry entry omits `owner:`, the portal reports itself as **unowned**,
and `test_portal_reports_its_own_ownership_gap` pins that. When a real owner is
assigned, that test should be *changed*, not deleted.

## Design notes

**One template, three nav links.** `/idp/`, `/idp/catalog` and `/idp/scorecards`
render the same page focused on a different section. Three near-identical
templates would have drifted; a `focus` variable and an anchor jump do not.

**`self` is reserved in Jinja.** The context key is `self_report`, not `self` —
Jinja binds `self` to the template's own block namespace, so a context variable
of that name is silently shadowed and every `self.x` renders as `Undefined`.
This cost one debug cycle and is commented at the source.

**Ungraded is `None`, never `0`.** A component with no scorecard result keeps
`score=None` and `level=None`. A zero would be indistinguishable from a real
failing grade — the same confusion the `health_probed` flag exists to prevent
one layer down.

**Degrade, don't 500.** A malformed scorecard, an unimportable adapter or an
unreachable database costs the page its grades and names the failure inline;
the catalog still renders. A portal that 500s tells an on-call engineer
strictly less than one that renders and says which signals are dark.

## Verification

```bash
# The gate itself
python -c "from tools.config.component_registry import validate_canvas_completeness as v; \
r = v('idp'); print(r.passed); [print(i.present, i.point, i.message) for i in r.items]"

# The portal's own grade
python tools/idp/scorecard.py --scorecard component-readiness --component idp

# Unit (26 tests, no DB fixture)
pytest tests/test_idp_portal.py -v

# E2E
npx playwright test tests/e2e/idp_portal.spec.ts
```

Routes exercised against a booted `create_app()`: `/idp/`, `/idp/catalog`,
`/idp/scorecards`, `/idp/component/idp` → 200; `/idp/component/<unknown>` → 404;
`/idp/api/catalog`, `/idp/api/scorecard`, `/idp/api/component/idp` → 200.

## Follow-on work this unblocks

* `idp-score-03` — persist score history; `developer_scorecards` flips to
  present in the signal strip and the page gains a trend.
* `idp-cat-02` — populate `kg_edges`; the detail page gains blast radius.
* `idp-gap-01` — a failing rule seeds a kanban task; the portal is where a
  failing rule becomes visible before it becomes work.
* `idp-score-05` — make the completeness gate block instead of warn. The portal
  is what makes a blocking gate legible when it fires.
