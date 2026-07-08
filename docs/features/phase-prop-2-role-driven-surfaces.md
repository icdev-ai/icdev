# Phase Prop-2 — Role-Driven GovCon / Proposals / CPMP Surfaces

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | Prop-2 |
| Title | Role-driven capture, proposal, and contract-performance surfaces |
| Status | Implemented |
| Priority | Critical |
| Dependencies | Phase 59 (GovCon Intelligence), Phase 60 (CPMP), Phase 62 (GovProposal RFx) |
| Tasks | prop-cap-11..14, prop-iqe-01, prop-fix-07..11, prop-sec-02, prop-ctr-02, prop-pm-01/02, prop-rev-* |

## Summary

Phase Prop-2 turns the GovCon (`/govcon`), Proposals (`/proposals`), and CPMP
(`/cpmp`) canvases into role-scoped, classification-aware workspaces. Every
page is gated by `RBAC_MATRIX` in `tools/dashboard/auth.py`, write endpoints
enforce role checks, and rendered pages carry a classification banner
**derived** from the record being displayed (via
`includes/classification_macros.html::design_classification_banner`), not a
hard-coded CUI banner.

## Features shipped

### Capture (prop-cap-11..14)
- **Capture phase-gate lifecycle** (`prop-cap-11`, dc128fbb2) — surface the
  capture plan gate progression (identify → qualify → capture → bid) on
  GovCon opportunity views, with test coverage of gate transitions
  (`pg_capture_plans`).
- **pWin model** (`prop-cap-12`, 5d30e13f4) — probability-of-win scoring on
  `/proposals/<opp_id>/pwin` (GET + POST in `tools/dashboard/api/govcon.py`);
  test-coverage gap closed.
- **Black-hat / PTW workspace** (`prop-cap-13`, 146cbb77d) — price-to-win
  workspace at `/proposals/<opp_id>/ptw`, restricted to
  `admin/capture_mgr/pm`.
- **BD pipeline view** (`prop-cap-14`, d9ea339a9) — pWin forecast, SAM.gov
  presolicitation feed, and CRM heat map on `/govcon`; registered the
  `forecast` IQE canvas (adapter productized by prop-vv-02).

### Reviews (prop-rev-*)
- **Color-team reviews with Gold sign-off** — `proposal_reviews` rows with
  `review_type='gold_team'`; reviews dashboard at
  `/proposals/reviews-dashboard` restricted to `admin/pm/reviewer`.

### Contracts & PM (prop-ctr-02, prop-pm-01/02)
- **Funding / obligation tracking + base+option periods** (7e091e38c,
  7146c9a1c) — `cpmp_contracts`, `cpmp_clins`, `cpmp_contract_periods`;
  obligation metrics exposed in portfolio detail (#93).
- **Contract modifications, IMS milestones, and program risk register**
  (prop-pm-02, 57b8bf6fe) — all render as sections of the single
  `/cpmp/<contract_id>` detail page.

### IQE (prop-iqe-01)
- `tools/iqe/adapters/govcon.py` and `tools/iqe/adapters/proposals.py`
  register the govcon/proposals collections; seed queries in
  `context/iqe/queries/govcon/` and `context/iqe/queries/proposals/`;
  broken PTW widget fixed (63a9f4b1b).

### Security (prop-fix-07..11, prop-sec-02)
- **RBAC page gating** — `RBAC_MATRIX` extended with `bd`, `capture_mgr`,
  `contract_mgr`, `reviewer`, `cor` roles; page-level route gating for
  GovCon/Proposals/CPMP (d68477727) and RBAC on proposals/cpmp/
  proposal_genesis write endpoints (90d7b0ecf).
- **MAC / derived classification** (prop-sec-02, a0dbf0bbd) —
  `g.security_context` wired into the auth flow; Bell-LaPadula MAC subject
  attributes (`dashboard_users.clearance_level`, `.compartments`) enforced
  on SECRET+ surfaces; `dashboard_users.role` CHECK constraint extended to
  match `RBAC_MATRIX`/`GOVLIFT_ROLES` (eddc43bcb, PR #110).
- **Tenant isolation** (prop-fix-07, 002446a96) — `tenant_id` backfill
  across proposal/govcon/cpmp tables.

## Role matrix (page-level)

| Page | Allowed roles |
|------|---------------|
| `/govcon`, `/govcon/requirements`, `/govcon/capabilities` | admin, bd, capture_mgr, pm, isso |
| `/proposals` (list/detail/sections/compliance/language) | admin, bd, capture_mgr, pm, reviewer |
| `/proposals/reviews-dashboard` | admin, pm, reviewer |
| `/proposals/<opp_id>/ptw` | admin, capture_mgr, pm |
| `/cpmp` (+detail/deliverables/reports) | admin, pm, developer, isso, co, contract_mgr |
| `/cpmp/cor` | admin, pm, isso, co, cor, contract_mgr |
| `/proposal_genesis` | admin, pm, bd, capture_mgr |

## Verification

See `docs/features/phase-prop-2-vv-gate.md` (prop-vv-02) for the full V&V
gate results: per-role E2E (`tests/e2e_govcon_proposals_cpmp.py`,
`tests/e2e_prop_vv02_role_and_new_surfaces.py`), behave workflows, IQE
smoke, bandit, coherence, and companion sync.
