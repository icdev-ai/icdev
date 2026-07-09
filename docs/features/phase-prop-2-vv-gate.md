# Phase Prop-2 — V&V Gate Results (prop-vv-02)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | Prop-2 (V&V gate) |
| Task | prop-vv-02 |
| Baseline | `icdev-prop-vv-02` @ 00f210dc2 (== origin/main, PRs #95–#110 included) |
| Date | 2026-07-08 |
| Status | PASS (4 pre-existing coherence failures documented, all outside prop scope) |

## Gate results

### 1. Per-role E2E (Selenium/Playwright) — PASS

`tests/e2e_govcon_proposals_cpmp.py` (prop-vv-02-d1): **48/48** against the
worktree server (SQLite verify DB `data/vv02_verify.db`, seeded
`dashboard_users` for admin/bd/capture_mgr/pm/reviewer/contract_mgr/developer):

- Template-source + live-render checks: DERIVED classification banner
  (`design_classification_banner(opp)`) on govcon pipeline, proposals
  list/detail, PTW, Language, CPMP portfolio/deliverables/reports.
- Role gates: `/govcon` allows admin/bd/capture_mgr/pm and denies others;
  write endpoints (extract-requirements, auto-compliance, auto-draft,
  bid-recommendation) reject unauthorized roles.
- No severe browser JS errors (after events.py fix below).
- Screenshots: `playwright/screenshots/{govcon_pipeline, proposals_list,
  proposals_ptw, proposals_language, proposals_reviews_dashboard,
  cpmp_portfolio, cpmp_deliverable_center, cpmp_reports}.png`

`tests/e2e_prop_vv02_role_and_new_surfaces.py`: **12/12** — genuine per-role
assertions with real role users (bd allowed /proposals + denied /cpmp;
developer allowed /cpmp + denied /proposals), plus the new surfaces: capture
gate (`pg_capture_plans`), color-team **Gold** sign-off
(`proposal_reviews.review_type='gold_team'`), and contract mod + IMS
milestone + risk register sections on `/cpmp/<contract_id>` with CUI banner
(screenshot `cpmp_contract_detail.png`).

### 2. behave proposal workflows — PASS

- `features/proposals_workflow.feature`: 8/8 scenarios (SQLite verify DB).
- `features/proposals_icdev_content.feature`: 9/9 scenarios (PostgreSQL
  backend — validates the ICDEV-branded seeded proposals incl. Gold Team
  Review content).

### 3. IQE smoke (govcon/proposals) — PASS

`tests/test_iqe_adapter_govcon_requirements.py`,
`tests/test_iqe_adapter_proposals.py`, `tests/test_iqe_seed_queries.py`,
`tests/test_proposals_iqe_query_api.py`: **29/29**.

### 4. Security / coherence / companion — PASS (with documented baseline exceptions)

- **bandit** (`-r tools/ --severity-level medium`): 1,260 findings, **all
  MEDIUM severity, zero HIGH/CRITICAL** — gate blocks on critical/high only.
- **companion sync** (`tools/dx/companion.py --sync --write`): 10/10
  platforms written, 63 skills translated.
- **coherence** (`coherence_checker.py --all --fix --gate`): 28 pass /
  4 fail / 4 warn. Fixed during this gate:
  - `iqe_map_sync` — `forecast` IQE adapter registered by prop-cap-14 but
    never committed; productized `tools/iqe/adapters/forecast.py` (+ icdev/
    mirror).
  - `karpathy_sync` — regenerated 10-platform companion configs in the
    verification tree.

  Remaining 4 failures are **pre-existing on main and outside prop scope**
  (verified present at baseline 00f210dc2 before any prop-vv-02 change):
  - `canvas_placeholder_style` / `log_standard` — 24 bare `?` placeholders +
    raw `logging.getLogger()` in `tools/ace/*` (ACE remediation in flight in
    a separate session; fixes exist uncommitted in the canonical checkout).
  - `new_page_completeness` — `logs` canvas missing components (EQO
    centralized-logging epic, tracked separately).
  - `component_registry` — 3 registry entries point at missing app
    blueprints (`apps/ai_gameday`, `apps/forge_academy`, `apps/innovation`),
    predates Phase Prop-2.

## Defects found & fixed by this gate

1. **`/api/events/poll` 500 on every dashboard page** —
   `tools/dashboard/api/events.py::_get_db()` hardcoded
   `db_path=BASE_DIR/data/icdev.db`, overriding `ICDEV_DB_PATH`; in any
   worktree/verify environment SQLite silently created an empty DB and the
   route threw `no such table: hook_events` (surfaced as severe JS console
   errors on /govcon, /proposals, /cpmp). Fixed to `get_connection()` so the
   storage layer resolves backend + env override. (Both namespaces.)
2. **Missing `forecast` IQE adapter** — see `iqe_map_sync` above.
3. **Language page lacked derived classification banner** —
   `proposals/language.html` now imports and calls
   `design_classification_banner(opp)` (both namespaces).

## Related docs

- `docs/features/phase-prop-2-role-driven-surfaces.md` — feature inventory
  for the Phase Prop-2 role-driven surfaces.
