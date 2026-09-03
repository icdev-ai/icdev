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

## Landed so far

| Card | Old URL | Governed home | Canvas | Test |
|---|---|---|---|---|
| rmf-ui-01 | `/ato-compliance` | `/boundary/ato-compliance` | BDC | `tests/test_bdc_ato_compliance_page.py` |
| rmf-ui-14 | `/prod-audit` | `/security/prod-audit` | SDC | `tests/test_sdc_prod_audit_page.py` |
| rmf-ui-15 | `/ai-transparency` | `/security/ai-transparency` | SDC | `tests/test_sdc_ai_transparency_page.py` |
| rmf-ui-12 | `/stig-manager` | `/security/stig-manager` | SDC (owns STIGs) | `tests/test_sdc_stig_manager_page.py` |
| rmf-ui-13 | `/sbd` | `/security/sbd` | SDC | `tests/test_sdc_sbd_page.py` |
| rmf-ui-07 | `/poam` | `/boundary/poam` | BDC | `tests/test_bdc_poam_page.py` |

`/prod-audit` is a visibility surface (production-readiness checks, read-only
posture), which is SDC's ground; its `/api/prod-audit/*` blueprint
(`tools/dashboard/api/prod_audit.py`) did not move. SDC's template directory is
`tools/dashboard/templates/security_canvas/` (the registry's completeness
template), and its wrapper is `sc_login_required`.

`/ai-transparency` is likewise a visibility surface (OMB M-25-21 / M-26-04 /
NIST AI 600-1 / GAO-21-519SP posture reporting: AI inventory, model cards,
cross-framework gaps); its `/api/ai-transparency/*` routes in `app.py` did not
move. No AI-governance canvas exists to prefer instead — `aimc` is the AI/ML
design catalog, `aadc` the default-off agentic-AI design canvas and
`ai_observatory` a telemetry adapter — so SDC, the card's named default, holds.

rmf-ui-13 is an SDC move: the CISA Secure by Design 8-pillar assessment
is hardening posture, a visibility surface, so it lands on the Security Design
Canvas behind `sc_login_required`. Its IQE widget is wired to the canvas's own
`/security/api/iqe-query` endpoint. The `/api/sbd/*` blueprint is unchanged.

`/poam` (rmf-ui-07) is the findings approval workflow across the seven canvas
DBs, an RMF artifact surface, so it lands on BDC. Its template already lived in
a subdirectory (`poam/list.html`) and moved as `boundary_canvas/poam/list.html`;
the `/api/poam/*` routes stay in `app.py`.
`tests/test_history.py::test_poam_route_exists` was NOT touched: it GETs the
NETWORK blueprint's own `/poam` (`/network/poam` at runtime), a different page
that never went through `app.py`.

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
