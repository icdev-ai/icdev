# NAV — Dashboard Menu Production-Readiness Sweep

**Project:** `nav-menu-readiness` (`args/projects.yaml`, task prefix `nav-`)
**Window:** 2026-07-18 → 2026-07-19 · **PRs:** #562–#613 (51 merged, zero red merges)
**Scope:** the 8 top-level dashboard menus — Build, Intelligence, Compliance, Strategos, Platforms, Studio, Updates, More — and their ~130 submenu pages. (Canvases menu excluded: covered by the CNR/PENTA waves. `/security/*` internals excluded: SHX-hardened.)

Three parallel production-readiness audits (dead links, auth/RBAC, fabricated data, fail-open handling, dead LLM paths, XSS, TRUST grounding, PG dialect) seeded a manually-gated kanban card (`nav-gate-00`); Opus 4.8 subagents implemented each task in isolated worktrees with push + PR and CI-gated merges.

## Security fixes

| Area | Fix | PR |
|------|-----|----|
| Env-key auto-login (P0) | Anonymous traffic no longer authenticated as admin; key must be presented (constant-time); dev auto-login behind explicit `ICDEV_DASHBOARD_DEV_AUTOLOGIN` | #567 |
| Tenant Admin Console (P0) | `_require_admin()` enforces admin unconditionally (was fail-open unless `ICDEV_ENFORCE_CANVAS_ACCESS`); covers components, SSO, API keys, GDPR erasure | #564 |
| Usage API (P0, issue #137) | Admin fallback removed; org-wide aggregates admin-only; non-admins scoped to own rows | #565 |
| RBAC on mutations | HITL approvals (#566), Strategos + ZIG (13 routes, #570), kanban scheduler / pulse editorial / AISG / foundry (#587), SRE operations incl. runbook execution + DORA rework (#600), GKP promote/reject (#589), compliance API family (#596), WriteGuard CRUD + Ask-ICDEV rate-limit (#607), attribution bound to session user (#609) | — |
| XSS | All confirmed sites fixed (intake PRD, Strategos panels/chat, pulse) with vendored DOMPurify (#592); systematic sweep — `markdown` filter chokepoint sanitize, `json_script_safe` filter, shared `esc.js`, 6 chat/assistant sinks (#602); Intelligence-menu sinks (#597) | — |

## Honesty fixes (no fabricated data)

- AISG suite: real ROI/executive/PM/compliance/skills data with honest empty states (was hardcoded 342.5 hrs/$51k, canned ATO/POA&M) — #576, #577; real handoff import + honest deploy labels — #590.
- TRUST grounding: INTSUM (#572) and OPORD (#573) now cite `[source: …]` validated by `citation_grounding`, with persisted verdicts, approval gating, and audited force-overrides (migrations 279/280).
- Oracle assessment returns `available:false`/503 instead of fabricated lens scores (#578); FedRAMP readiness reports "Insufficient Scope" instead of 100% on zero scoreable controls (#574, per-family N/A #583); FathomDesk chart carries `simulated:true` + banner (#598); GeoSIGINT layers labeled static-reference with vintage (#582, badge render fix pending `nav-plat-06`); slides decks report `degraded`/`template` with per-slide provenance (#603); translation gates verify for real and mark not-verified states (#601, #606); autoresearch/ontology stub outputs flagged heuristic (#608); macro intelligence 503s instead of fake NEUTRAL (#593); compliance scores stop inflating (poam not-assessed, heuristic labels) (#609).

## Reliability / correctness

- Ten dead `LLMRouter.complete()` call sites repaired (+2 more caught by the new `check_llm_router_api` coherence rule) — #569, #586.
- Fail-loud degraded-state pattern (`docs/dev/degraded-state-pattern.md`): finetune/connector-forge (#595), 13 more page/API endpoints (#604), compliance counters (#596), `/evidence` fixed from 500-on-every-request (#605).
- PG-dialect repairs: admin burst detection (#579), IW scorers (#575), autoresearch placeholders (#610), translations persistence (#591), 4 more sites (#597).
- Kanban CLI no longer silently operates on a foreign repo's database (marker-walk root + shadow guard) — #584.
- Legacy `tools/strategos/blueprint.py` removed; four wargame endpoints that 404'd live were ported — #585.
- `app.py` decomposition begun: pulse/research/clawhub blueprints extracted (65 routes, −1,651 lines, URL map byte-identical) — #611.
- Pre-existing test failures fixed: `test_aisg_wizard.py` (#599), `test_autoresearch.py` (#610).

## Verification

- Every PR: unit tests (deny cases mandatory for RBAC), ruff, full CI (Lint, Test, Test-PostgreSQL, Security Scan, Helm Lint, E2E).
- End-of-wave Playwright sweep (`tests/e2e/nav_menu_readiness.spec.ts` + honesty/regression specs, 19 tests) — #612; it immediately caught two dead nav links (`/integrity`, `/foundry` rendered while env-gated), root-caused and fixed via registry-driven nav gating — #613.

## Known follow-ups (on the board, gated behind `nav-gate-00`)

- `nav-plat-06` — GeoSIGINT provenance badge blocked by a `base.html` template-namespace shadow (encoded as `test.fail` in the E2E spec).
- Open product decision: the pulse blog LLM judge is advisory-only; whether a RED verdict should block publishing needs an owner call.

## Operational notes

- E2E/CI authenticates via `ICDEV_DASHBOARD_DEV_AUTOLOGIN=true` (set in `icdev-ci.yml` and `playwright.config.ts`); local single-user dev wanting the old auto-login behavior must set it in `.env`.
- The long-running dashboard process must be restarted to serve the merged wave.
