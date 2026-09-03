# Governed compliance routes — one per card (rmf-ui-01..16)

## The defect

About fifteen compliance pages were bare `@app.route` handlers in a 10,500-line
`tools/dashboard/app.py`: no `args/component_registry.yaml` entry, **no RBAC
guard**, no completeness gate, no IQE dispatch, no posture card. Each rendered a
real template over a real API and each was reachable by anyone who could reach
the dashboard.

Two canvases already own that ground. The Boundary & Supply Chain Canvas (BDC,
`/boundary`) owns ATO boundary, enclave and cross-domain with real data
(`bd_*` tables, cATO readiness, the fabric posture roll-up). The Security Design
Canvas (SDC, `/security`) owns the Zero Trust pillars and STIGs. So the routes
move onto those two — RMF artifact surfaces to BDC, visibility surfaces to SDC —
and **no fourth canvas is scaffolded**.

## Why one route per card

A fifteen-route move is unreviewable, and its failure mode is a silently dropped
page: a template that stops being rendered anywhere produces no import error, no
route-coverage finding and no test failure. One route per card gives each move
its own red-first proof, its own browser verification and its own diff a reviewer
can hold in their head. rmf-ui-01 is the exemplar; rmf-ui-03..16 each carry one
of the remaining routes and depend on rmf-ui-01 so the exemplar lands first.

## What a migrated route gets, by construction

A route declared on a canvas blueprint inherits every governance property the
bare handler lacked, without any per-route wiring:

| Property | Where it comes from |
|---|---|
| Registry entry | the canvas's existing `args/component_registry.yaml` block |
| RBAC guard | `app.py` attaches `guard_component_access(<key>, min_il)` as a `before_request` on every registered canvas blueprint (auth fail-closed, impact-level check, grant seeded from `default_roles`), plus the canvas's own `*_login_required` wrapper on the route |
| Completeness gate | `new_page_completeness` (mirror parity for every template under the canvas directory) and `canvas_completeness` (the registry-driven 8-point gate) |
| IQE dispatch | the registry's `url_prefix` + IQE adapter put `<prefix>/*` on the client-side path→canvas map; the template includes `includes/iqe_query_widget.html` |

## The exemplar: `/ato-compliance` → `/boundary/ato-compliance`

- **Route** — `bdc_ato_compliance_page` in `tools/boundary_canvas/blueprint.py`,
  behind `bdc_login_required`, rendering `boundary_canvas/ato_compliance.html`.
  The page drives the unchanged `/api/ato-compliance/*` blueprint; only the
  page route moved.
- **Old URL** — `@app.route("/ato-compliance")` stays in `app.py` as a
  `301` redirect to the governed home. A bookmark, an e2e spec or a stale href
  lands on the page, never a 404. The handler no longer calls
  `render_template`, and a test walks its AST to keep it that way.
- **Nav** — both `base.html` copies (`tools/` and `icdev/`) link the new path
  and list it in the Compliance dropdown's active-path list; `compliance.html`
  (the hub page that links its siblings) is repointed too.
- **Mirror** — blueprint, `app.py`, `base.html`, the moved template and the
  repointed hub template are byte-identical under `icdev/`; the old top-level
  template is deleted from both trees.
- **Tests** — `tests/test_bdc_ato_compliance_page.py` (gated via
  `args/ci_test_files/core.d/rmf-ui-01.txt`) proves the render, the RBAC
  refusal (both the wrapper and the registry guard), the redirect, the nav in
  both copies, the mirror, and the IQE path→canvas dispatch. All three touched
  test files are RED on the merge base per `red_first_gate.py`.

## The shape the follow-up cards copy

1. Add the route next to the exemplar's block on the target blueprint; `git mv`
   the template into the canvas's template directory; add the IQE widget include
   and a breadcrumb back to the canvas root.
2. Replace the `app.py` handler body with `redirect(<new>, code=301)`; keep the
   decorator.
3. Repoint the href in BOTH `base.html` copies, add the new path to the
   dropdown's active list, `grep -rl 'href="<old>"'` the templates for other
   links, and add the path to the `Pages:` line in `.claude/commands/start.md`
   and to `tests/e2e/nav_intelligence_compliance.spec.ts`.
4. Copy every touched file to `icdev/`; `git rm` the old `icdev/` template.
5. Clone the exemplar test file; gate it with a `core.d/<task-id>.txt`
   fragment; fix every existing test naming the old path
   (`tests/compliance/test_compliance_surface_liveness.py::GOVERNED_HOME` is the
   pattern for a migrated orphan); run `red_first_gate.py --gate`.
6. `coherence_checker.py --check new_page_completeness --gate` and
   `--check canvas_completeness --gate` both exit 0; `ruff check` the changed
   set; verify in a browser.

Every follow-up card touches `base.html` and `compliance.html`, so sibling PRs
will report DIRTY against each other. Rebase; do not fight the verdict.

## rmf-ui-05: `/cato` → `/boundary/cato-health`

The second route to move, and the first with a fold-or-land decision. BDC
already served `/boundary/cato` (the per-design "cATO Dashboard": a table of
`bd_assessments` scores and the fabric posture roll-up, in the canvas's dark
style). The top-level `/cato` was the OTHER cATO view: a fleet-wide health
gauge, evidence stream, control-family heatmap, certifications and timeline over
nine `/api/cato/*` calls, in the dashboard's light style. Folding a 530-line
page with its own data model into a 250-line page with a different one would
have produced one page with two themes and two ideas of what "a score" is, so
it lands at `/boundary/cato-health` under a DISTINCT title ("Continuous ATO
Health"), cross-linked to `/boundary/cato` from its lede.
`tests/test_bdc_cato_health_page.py` pins that the two title blocks differ and
that the new one is not "cATO Dashboard". Everything else copies the exemplar:
`bdc_cato_health_page` on the blueprint, a 301 from `app.py`, both `base.html`
copies plus `compliance.html` and `mosa.html` repointed, the template moved and
mirrored, `core.d/rmf-ui-05.txt`.
