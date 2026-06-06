# CUI // SP-CTI
"""Seed Kanban tasks for the GovCon / Proposals FOUO+ compliance hardening.

Project: prop  (task_prefix 'prop-')  — Proposals Module Enhancement.
Plan: C:/Users/schuo/.claude/plans/analyze-the-following-for-stateless-mitten.md

IMPORTANT — the prop project was ALREADY partially built (prop-fix-01..06,
prop-cap-01..10, prop-cmp-01..10, prop-rev-01..08 are all status='done'). This
wave adds only NET-NEW work: the access-control HARDENING never done (tenant_id
RLS, @require_role wiring, ABAC + column masking, MAC), a platform-wide
classification-AGGREGATION / compilation guard (mosaic defense), and net-new
role redesigns (capture phase-gates, real pWin model, black-hat/PTW, White team
+ Gold sign-off gate, contract mods, lightweight IMS, program risk register,
IQE integration). New IDs sit ABOVE the existing done ranges (fix-07+, cap-11+,
rev-09+); the cmp epic is omitted (already complete).

Task IDs follow <prefix><epic>-<N> so tools/project/kanban_project_sync.py
auto-populates args/projects.yaml epics. depends_on_task_id is single-parent;
secondary prerequisites are named in the description.

Run:
    python tools/kanban/seed_prop_security_kanban.py --dry-run
    python tools/kanban/seed_prop_security_kanban.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

PROJECT_ID = "prop"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # ---- EPIC disco -------------------------------------------------------
    {
        "id": "prop-disco-01",
        "title": "Discovery — run Innovation/Creative/Research engines (GovCon proposal + classification-aggregation) and curate findings",
        "description": (
            "Phase 0 discovery. Run (or read captured output of) the three engines scoped to DoD/IC "
            "capture-and-proposal management AND the classification-aggregation/compilation (mosaic) problem, "
            "then distill a curated adaptation list into docs/features/prop-discovery.md: "
            "(1) Innovation: `python tools/skills/invoke.py --exec icdev-innovate -- --json`; "
            "(2) Research: `python tools/skills/invoke.py --exec icdev-research -- --json` focused on "
            "Shipley/APMP capture+proposal best practice, pWin modeling, color-team workflows, and DoD 5200.01 "
            "derivative-classification / 32 CFR 2002 CUI aggregation practice; (3) Creative per goals/manifest.md "
            "(tools/creative/creative_engine.py --run --domain \"government proposal management\" --json). Map "
            "each surfaced pain-point to an existing prop-* task or a NEW prop-* task. Output is the checklist the "
            "rest of the epics implement against. No production code changes in this task."
        ),
        "task_type": "research",
        "priority": "high",
        "depends_on_task_id": None,
    },

    # ---- EPIC fix (NET-NEW fix-07+) ---------------------------------------
    {
        "id": "prop-fix-07",
        "title": "Migration — add tenant_id (+confirm classification) to proposal/govcon/cpmp/pg tables; mirror init_db + conftest",
        "description": (
            "New migration under tools/db/migrations/ adding tenant_id TEXT (and confirming classification TEXT "
            "DEFAULT 'CUI') to every proposal/govcon surface table: proposal_opportunities, proposal_volumes, "
            "proposal_sections, proposal_section_dependencies, proposal_compliance_matrix, proposal_reviews, "
            "proposal_review_findings, proposal_section_drafts, proposal_knowledge_base, proposal_amendments, "
            "proposal_competitors, proposal_questions, proposal_question_responses, proposal_shred_items, "
            "proposal_teaming_partners, proposal_versions, proposal_status_history; sam_gov_opportunities, "
            "rfp_shall_statements, rfp_requirement_patterns; cpmp_* tables; pg_* tables. Idempotent / "
            "table-existence-graceful (SQLite + PostgreSQL; ADD COLUMN IF NOT EXISTS per backend). Mirror in "
            "tools/db/init_icdev_db.py DDL + tests/conftest.py MINIMAL_ICDEV_SCHEMA. NOTE kanban-tasks-lock-storm: "
            "run offline, not against a busy daemon; use get_connection() (RLS-aware main DB). Unblocks RLS "
            "predicate injection."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-disco-01",
    },
    {
        "id": "prop-fix-08",
        "title": "RBAC — extend RBAC_MATRIX + add proposal roles (bd, capture_mgr, contract_mgr, pm, reviewer)",
        "description": (
            "In tools/dashboard/auth.py (~320-372) extend RBAC_MATRIX with govcon/proposals/cpmp/proposal-genesis "
            "page keys and add roles bd, capture_mgr, contract_mgr, pm, reviewer (alongside admin/isso/co/cor). "
            "Define which roles view each page (proposals_list -> {admin,bd,capture_mgr,pm,reviewer}; cpmp -> "
            "{admin,pm,contract_mgr,co,cor,isso}). Keep require_role(*roles) semantics. No route wiring yet. "
            "Requires prop-fix-07."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-fix-07",
    },
    {
        "id": "prop-fix-09",
        "title": "RBAC wiring — @require_role on /govcon + /govcon/* routes and api/govcon.py endpoints",
        "description": (
            "Apply @require_role(...) to govcon page routes in tools/dashboard/app.py (govcon_pipeline_page "
            "/govcon, govcon_requirements_page, govcon_capabilities_page) and write/sensitive endpoints in "
            "tools/dashboard/api/govcon.py (sam/scan, import, extract-requirements, map-capabilities, "
            "auto-compliance, bid-recommendation, auto-draft). Roles per fix-08. 403 (not 500) + log_auth_event on "
            "deny. Requires prop-fix-08."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-fix-08",
    },
    {
        "id": "prop-fix-10",
        "title": "RBAC wiring — @require_role on /proposals + /proposals/* routes and api/proposals.py endpoints",
        "description": (
            "Apply @require_role(...) to all proposals routes in tools/dashboard/app.py (proposals_list_page "
            "/proposals ~line 444, proposals_detail_page, proposals_section_detail_page, proposals_compliance_gaps, "
            "proposals_reviews_dashboard) and CRUD/transition endpoints in tools/dashboard/api/proposals.py "
            "(opportunities, volumes, sections, status transitions, compliance, reviews, findings, questions, "
            "amendments). 403 + log on deny. Requires prop-fix-08."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-fix-08",
    },
    {
        "id": "prop-fix-11",
        "title": "RBAC wiring — @require_role on /cpmp* and /proposal-genesis routes + their API endpoints",
        "description": (
            "Apply @require_role(...) to cpmp_portfolio_page (/cpmp), cpmp_detail_page, cpmp_deliverable_detail_page, "
            "proposal_genesis (/proposal-genesis) in tools/dashboard/app.py, plus tools/dashboard/api/cpmp.py and "
            "tools/dashboard/api/proposal_genesis.py (reflex run, pipeline, set-config admin/pm-gated). Reuse "
            "existing cpmp/cpmp_cor RBAC_MATRIX entries. 403 + log on deny. Requires prop-fix-08."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-fix-08",
    },
    {
        "id": "prop-fix-12",
        "title": "RLS — convert raw SELECT * reads to security-context-aware reads; verify get_connection (RLS on)",
        "description": (
            "Across govcon/proposals/cpmp routes (tools/dashboard/app.py) and API blueprints, ensure every read "
            "runs under the request security context so tools/db/storage.py StorageCursor._inject_rls applies the "
            "tenant_id + classification predicates from prop-fix-07. Confirm _get_db() -> get_connection() "
            "(RLS-aware), NOT get_canvas_connection(). Replace 'SELECT * FROM proposal_opportunities ORDER BY "
            "due_date' (app.py ~449) and siblings with predicate-injected reads. Verify "
            "_attach_flask_security_context bridges g.security_context (the earlier prop-fix-01 removed a "
            "set_security_context(None) override; confirm context now flows). Follow "  # rls-bypass: text in task description, not an actual call
            "feedback_always_use_get_connection + rls-consideration-in-code-generation. Requires prop-fix-07; "
            "secondary prop-fix-09/10/11."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-fix-07",
    },

    # ---- EPIC sec ---------------------------------------------------------
    {
        "id": "prop-sec-01",
        "title": "ABAC + column masking — proposal policies in security_config.yaml + @abac_protect on owned rows",
        "description": (
            "Define proposal ABAC + column-masking policies in args/security_config.yaml. Column policies "
            "(tools/security/column_security.py + StorageCursor._apply_column_masking) mask sensitive columns by "
            "role: cost/price (cpmp_clins.value, pg_cost_volumes), win-strategy/discriminators (pg_win_themes), "
            "competitor intel (proposal_competitors, pg_*), PTW — full for capture_mgr/admin, masked for "
            "reviewer/co. ABAC (tools/security/abac_engine.py) enforces need-to-know: a section writer "
            "(proposal_sections.writer_email == subject.user_id) edits only assigned sections. Apply "
            "@abac_protect(resource_attr_fn, action) on section edit + draft endpoints in api/proposals.py. "
            "Requires prop-fix-08; secondary prop-fix-12."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-fix-08",
    },
    {
        "id": "prop-sec-02",
        "title": "MAC — @require_clearance / compartment checks on SECRET+ surfaces; thread COI/LAC/SCI compartments",
        "description": (
            "Apply Bell-LaPadula MAC (tools/security/classification_enforcer.py: @require_clearance, "
            "@require_compartment, can_read/can_write) to any proposal/cpmp surface that may hold SECRET or TS//SCI "
            "material. Ensure compartments (COI_*, LAC_*, SCI) flow from auth -> g.security_context "
            "(tools/security/security_context.py already supports frozenset compartments + clearance_level) so "
            "no-read-up / no-write-down + strict-subset compartment checks apply. SECRET/TS//SCI readiness layer. "
            "CUI/FOUO stays enforced via RLS/RBAC. Requires prop-sec-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-sec-01",
    },
    {
        "id": "prop-sec-03",
        "title": "Aggregation SCG rules — args/classification_aggregation.yaml (co-occurrence/count/window rules)",
        "description": (
            "Author the declarative Security Classification Guide (SCG) aggregation ruleset "
            "args/classification_aggregation.yaml. Rule forms: (a) FIELD CO-OCCURRENCE — result set with columns "
            "{A,B,C} together -> derived classification X (e.g. capture_plan + competitor PTW + incumbent pricing "
            "-> SECRET); (b) COUNT/THRESHOLD over window — >=N opportunities for agency X within window W -> "
            "elevate; (c) ENTITY-DIVERSITY -> elevate. Each rule = {name, when:{tables[], columns[], count?, "
            "window?, operator}, derive:<classification>, action: warn|block, rationale}. Reuse operator vocab in "
            "tools/security/abac_engine.py (>=, in, contains_any, contains_all). Admin-editable config, no Python "
            "classification logic. Requires prop-disco-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-disco-01",
    },
    {
        "id": "prop-sec-04",
        "title": "aggregation_guard.py — derived-classification computer + SCG rule evaluator (platform core)",
        "description": (
            "Create tools/security/aggregation_guard.py (platform-wide core). compute_derived_classification("
            "elements, *, ctx=None) -> classification: max of (a) each element's stored classification and (b) any "
            "args/classification_aggregation.yaml SCG rule that fires across the element set. Use "
            "tools/compliance/classification_manager.get_clearance_order for the lattice "
            "(PUBLIC<CUI<SECRET<TS<TS//SCI). evaluate_rules(result_set) -> [fired_rule]. Pure, deterministic, no "
            "LLM. Importable from route/service layer and optionally as a StorageCursor post-fetch hook. Requires "
            "prop-sec-03; secondary prop-fix-07."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-sec-03",
    },
    {
        "id": "prop-sec-05",
        "title": "aggregation_events — append-only audit table (migration + init_db + conftest + APPEND_ONLY)",
        "description": (
            "Add append-only aggregation_events table (id, occurred_at, user_id, tenant_id, surface/route, "
            "rule_name, derived_classification, surface_ceiling, action ('derive'|'warn'|'block'), element_summary, "
            "classification) via migration + tools/db/init_icdev_db.py + tests/conftest.py MINIMAL_ICDEV_SCHEMA. "
            "CRITICAL: add 'aggregation_events' to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py (never "
            "UPDATE/DELETE — NIST AU). Provide a write helper used by the guard. Requires prop-sec-04."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-sec-04",
    },
    {
        "id": "prop-sec-06",
        "title": "Mosaic guard — per-user volume/diversity throttle + block/warn at result-assembly & export",
        "description": (
            "Extend tools/security/aggregation_guard.py with the runtime re-aggregation/mosaic guard: before a "
            "combined view or export is returned, run compute_derived_classification; if derived > surface ceiling "
            "OR > requesting user's clearance_level -> block or warn (per rule action) and write an "
            "aggregation_events row. Add a per-user volume/diversity throttle (rows x distinct-entities x "
            "time-window) to blunt incremental scraping; when throttled, LOG what was withheld (NO silent caps — "
            "CLAUDE.md). Expose guard_result(result_set, ctx, surface) -> {derived, action, throttled, "
            "events_written}. Requires prop-sec-05; secondary prop-sec-04."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-sec-05",
    },
    {
        "id": "prop-sec-07",
        "title": "Wire aggregation guard into govcon/proposals read+export paths; render DERIVED classification banner",
        "description": (
            "Wire tools/security/aggregation_guard.guard_result into govcon/proposals/cpmp read paths and export "
            "endpoints (tools/dashboard/app.py + api blueprints). Replace the static CUI banner with the DERIVED "
            "classification banner via tools/compliance/classification_manager (render context passes "
            "derived_classification to base.html). On 'block' return a 403-style ceiling response; on 'warn' "
            "annotate the view. Integration gate that all Phase-2 feature tasks depend on. Requires prop-sec-06; "
            "secondary prop-fix-12."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "prop-sec-06",
    },
    {
        "id": "prop-sec-08",
        "title": "Register aggregation guard — security_gates.yaml gate + MCP registry + manifest + commands.md",
        "description": (
            "8-point registration for the aggregation guard: (1) 'aggregation_guard' gate in "
            "args/security_gates.yaml (block when derived-class > surface ceiling); (2) register in "
            "tools/mcp/tool_registry.py + gap_handlers.py; (3) manifest entry under tools/manifest/<security>.md + "
            "index line in tools/manifest.md; (4) CLI in docs/reference/commands.md; (5) coherence_checker "
            "sandbox-coverage decision if it ingests user content. (companion sync + coherence in prop-vv-01.) "
            "Requires prop-sec-07."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "prop-sec-07",
    },
    {
        "id": "prop-vv-01",
        "title": "Phase-1 gate — RLS/RBAC/ABAC/column + aggregation tests, role smoke, bandit + coherence",
        "description": (
            "Phase-1 verification gate (MUST be green before Phase-2 tasks are picked up). (1) Extend "
            "tests/test_row_security.py + test_rls_integration.py with proposal tables (tenant + classification "
            "isolation). (2) New tests/test_aggregation_guard.py: SCG rule fires -> derived=SECRET, banner "
            "upgrades, view blocks for a CUI user, aggregation_events row written; mosaic throttle trips -> warn + "
            "audit; no silent cap. (3) RBAC 403 tests for govcon/proposals/cpmp routes per role. (4) Playwright "
            "role smoke on /govcon, /proposals, /cpmp (screenshots to playwright/screenshots/). (5) "
            "`python -m bandit -r tools/ --severity-level medium`; `python tools/workflow/coherence_checker.py "
            "--all --fix --gate`; `python tools/dx/companion.py --sync --write --json`. Requires prop-sec-08; "
            "secondary prop-fix-09/10/11."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "prop-sec-08",
    },

    # ---- EPIC cap (NET-NEW cap-11+) ---------------------------------------
    {
        "id": "prop-cap-11",
        "title": "Capture phase-gate workflow on pg_capture_plans (qualify->pursue->capture->bid->proposal) + gate audit",
        "description": (
            "Surface pg_capture_plans as a managed phase-gate lifecycle (Shipley gates: qualify -> pursue -> "
            "capture -> bid -> proposal). Add a gate-decision state machine + append-only gate-decision audit "
            "(reuse proposal_status_history pattern). Builds beyond the existing basic Capture tab (prop-cap-07). "
            "Reuse tools/govcon/opportunity_lifecycle.py where present. If a new page, ship all 8 components per "
            "CLAUDE.md incl. IQE. Gated @require_role(capture_mgr, admin) + RLS + aggregation guard. Requires "
            "prop-sec-07; secondary prop-vv-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-sec-07",
    },
    {
        "id": "prop-cap-12",
        "title": "pWin model + weighted pipeline value",
        "description": (
            "Add a probability-of-win (pWin) MODEL (prop-cap-10 only color-codes a stored win %). Deterministic "
            "logistic/weighted score over capture-plan signals (incumbency, CRM engagement, competitive position, "
            "compliance coverage, past-performance fit) — extend tools/govcon/bayesian_bid_scorer.py. Feed pWin "
            "into a weighted pipeline value roll-up on the govcon pipeline view; show factor breakdown. Requires "
            "prop-cap-11."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-cap-11",
    },
    {
        "id": "prop-cap-13",
        "title": "Black-hat / PTW panel (reuse competitor_profiler + bayesian_bid_scorer)",
        "description": (
            "Competitive black-hat / price-to-win (PTW) workspace reusing tools/govcon/competitor_profiler.py "
            "(award history, agency/NAICS diversity, leaderboard) and tools/govcon/bayesian_bid_scorer.py — beyond "
            "the existing competitors sub-table (prop-cap-08). Model likely competitor approaches + price posture "
            "per opportunity. PTW/competitor columns masked per prop-sec-01 and may trip an SCG aggregation rule "
            "(prop-sec-03). Requires prop-cap-11."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-cap-11",
    },
    {
        "id": "prop-cap-14",
        "title": "BD pipeline view — weighted forecast + SAM.gov forecast-notice feed + CRM heat",
        "description": (
            "Business Development view on /govcon: weighted pipeline forecast (from pWin, prop-cap-12), a SAM.gov "
            "FORECAST-notice feed (extend tools/govcon/sam_scanner.py to pull forecast/presolicitation notices), "
            "and CRM engagement heat from pg_crm_accounts/contacts/interactions/engagement_scores on the pipeline "
            "cards. Requires prop-cap-12; secondary prop-cap-13."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-cap-12",
    },

    # ---- EPIC rev (NET-NEW rev-09+) ---------------------------------------
    {
        "id": "prop-rev-09",
        "title": "HITL reviewer assignment + hand-off on top of proposal_reviews/findings",
        "description": (
            "Add HITL reviewer ASSIGNMENT + hand-off (assign, notify, accept, reassign) on proposal_reviews — the "
            "existing prop-rev-08 built finding-closure-with-evidence but not the assignment/hand-off layer. "
            "Complements the AI color-team simulation in tools/govcon/color_review_simulator.py. Gated "
            "@require_role(reviewer, admin). Surface on /proposals/<id> reviews tab + reviews-dashboard. Requires "
            "prop-sec-07; secondary prop-vv-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-sec-07",
    },
    {
        "id": "prop-rev-10",
        "title": "Add White team to REVIEW_TYPES + Gold-team sign-off gate blocking 'submitted'",
        "description": (
            "Add a White team (compliance-check) review type to REVIEW_TYPES (tools/dashboard/api/proposals.py + "
            "color_review_simulator) and a Gold-team executive sign-off GATE that blocks the opportunity status "
            "transition to 'submitted' until Gold review status == passed. Enforce in the status transition "
            "endpoint. Requires prop-rev-09."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-rev-09",
    },

    # ---- EPIC ctr (NEW) ---------------------------------------------------
    {
        "id": "prop-ctr-01",
        "title": "cpmp_contract_mods table + modification request/approval workflow",
        "description": (
            "Add cpmp_contract_mods (mod_number, contract_id, type (admin/funding/scope/pop), description, "
            "value_delta, status (requested/in_review/approved/rejected/executed), requested_by, approved_by, "
            "dates, classification, tenant_id) via migration + init_db + conftest. Request/approval workflow + "
            "append-only status history (reuse cpmp_status_history), wired into /cpmp/<contract_id> tabs. Gated "
            "@require_role(contract_mgr, co, admin) + RLS. Requires prop-sec-07; secondary prop-vv-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-sec-07",
    },
    {
        "id": "prop-ctr-02",
        "title": "Funding/obligation tracking on cpmp_contracts + base+option period handling",
        "description": (
            "Extend cpmp_contracts with funding/obligation tracking (obligated_value, funded_value, "
            "remaining_obligation) and base+option period structure (period_type, option_number, pop_start, "
            "pop_end). Surface burn-rate vs obligation on contract detail + portfolio (reuse "
            "tools/govcon/portfolio_manager.py + evm_engine.py). Requires prop-ctr-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-ctr-01",
    },

    # ---- EPIC pm (NEW) ----------------------------------------------------
    {
        "id": "prop-pm-01",
        "title": "Lightweight IMS — milestones + dependencies linked to WBS/EVM",
        "description": (
            "Add a lightweight Integrated Master Schedule: cpmp_milestones (milestone, wbs_id ref cpmp_wbs, "
            "baseline_date, forecast_date, actual_date, status) + cpmp_milestone_deps (predecessor/successor) via "
            "migration + init_db + conftest. Link to cpmp_wbs + cpmp_evm_periods so EVM ties to a schedule. "
            "Surface a milestone/Gantt-lite view on /cpmp/<contract_id>. Gated @require_role(pm, admin) + RLS. "
            "Requires prop-sec-07; secondary prop-vv-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-sec-07",
    },
    {
        "id": "prop-pm-02",
        "title": "Program risk register",
        "description": (
            "Add a program-level risk register (cpmp_risks: title, category, probability, impact, exposure, "
            "mitigation, owner, status, classification, tenant_id) via migration + init_db + conftest. Surface a "
            "risk matrix on the contract/portfolio view; optionally link risks to milestones (prop-pm-01) and "
            "negative events (tools/govcon/negative_event_tracker.py). Requires prop-pm-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "prop-pm-01",
    },

    # ---- EPIC iqe (NEW) ---------------------------------------------------
    {
        "id": "prop-iqe-01",
        "title": "IQE — adapters for govcon + proposals + _CANVAS_MAP + PATH_CANVAS + seed queries",
        "description": (
            "govcon/proposals are NOT in the IQE _CANVAS_MAP today. Create tools/iqe/adapters/govcon.py and "
            "tools/iqe/adapters/proposals.py registering read-only collections returning REAL rows "
            "(proposals.opportunities, proposals.sections, proposals.compliance, proposals.reviews; "
            "govcon.opportunities, govcon.requirements, govcon.awards) — mirror tools/iqe/adapters/"
            "ai_augmentation.py. Add 'govcon' and 'proposals' to _CANVAS_MAP in tools/dashboard/app.py "
            "(~2714/2735), PATH_CANVAS entries in base.html, {% include 'includes/iqe_query_widget.html' %} in the "
            "govcon/proposals templates with a POST /api/iqe-query path, and >=3 seed queries each under "
            "context/iqe/queries/{govcon,proposals}/. All IQE reads run under RLS + aggregation guard. Requires "
            "prop-fix-10; secondary prop-sec-07."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "prop-fix-10",
    },

    # ---- EPIC vv ----------------------------------------------------------
    {
        "id": "prop-vv-02",
        "title": "Phase-2 gate — per-role Playwright E2E + behave + IQE smoke + bandit/coherence/companion + feature docs",
        "description": (
            "Final V&V gate for the role-driven features. (1) Playwright E2E per role on /govcon, /proposals, "
            "/cpmp and the new surfaces (capture gate, pWin/PTW, color-team Gold sign-off, contract mod, IMS "
            "milestone, risk register) — assert role-scoped views and that the rendered banner reflects DERIVED "
            "classification (screenshots to playwright/screenshots/). (2) behave proposal workflows. (3) IQE smoke "
            "for govcon/proposals collections. (4) `python -m bandit -r tools/ --severity-level medium`; "
            "`python tools/workflow/coherence_checker.py --all --fix --gate`; `python tools/dx/companion.py --sync "
            "--write --json`. (5) feature docs docs/features/phase-prop-*.md. All gates green. Requires "
            "prop-iqe-01; secondary: all cap-11+/rev-09+/ctr/pm tasks."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "prop-iqe-01",
    },
]


def seed(dry_run: bool = False) -> int:
    now = _now()
    # Preserve original behavior: every prop task is seeded status='scheduled'
    # with a real scheduled_at timestamp and CUI classification.
    tasks = [
        {**t, "status": "scheduled", "scheduled_at": now, "classification": "CUI"}
        for t in TASKS
    ]
    report = create_tasks(
        PROJECT_ID, tasks, dry_run=dry_run, strict=False, register_project=False
    )
    print(report.summary())
    return len(report.seeded)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed prop (Proposals/GovCon security) kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
