#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: Workflow Narrative Engine — 14 tasks, 5 epics.

  wne-meta-*   — YAML schema extension + ai_ml_transformation template
  wne-core-*   — Engine modules (context, narrative, COA, ROI, export)
  wne-chat-*   — Chat interface (/studio/narrate) built on chat_manager.py
  wne-api-*    — Studio toolbar integration
  wne-vv-*     — V&V gate

Dependencies on existing Kanban queue:
  wne-meta-01 depends on wfs-schema-01
  wne-meta-02 depends on wfs-decomp-01 (composite nodes needed for coa_* sub_steps)

Run: python tools/db/seeds/seed_wne_plan.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()


def _task(
    task_id: str,
    title: str,
    description: str,
    task_type: str,
    priority: str = "medium",
    depends_on: str | None = None,
) -> tuple:
    return (
        task_id, title, description, task_type, priority,
        "scheduled", NOW, "claude_cli", "wne_plan",
        depends_on, NOW, NOW,
    )


INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""

TASKS: list[tuple] = [

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC META — Schema extension + template
    # Depends on: wfs-schema-01 (README exists), wfs-decomp-01 (composite type)
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wne-meta-01",
        "Extend args/workflow_templates/README.md — add narrative_context block spec",
        (
            "Append a 'narrative_context' section to args/workflow_templates/README.md "
            "(file created by wfs-schema-01). Document all fields: "
            "  audience: leadership | technical | compliance | board | customer "
            "  org_name: string "
            "  program_name: string "
            "  classification: string (default CUI) "
            "  purpose: string (one sentence) "
            "  timeframe_months: int "
            "  parameters: dict with keys: "
            "    workforce_size, developers_targeted, avg_annual_salary_usd, "
            "    contract_value_usd, ai_productivity_gain_pct, "
            "    training_cost_per_person_usd, lab_standup_cost_usd, "
            "    free_resources_budget_usd, paid_courses_per_person_usd, ilt_cost_per_person_usd "
            "All fields are optional — WNE degrades gracefully when omitted. "
            "Update tools/studio/template_linter.py: "
            "  - if narrative_context present, validate audience is in allowed enum "
            "  - if parameters present, validate all values are numeric "
            "Only touch README.md and template_linter.py."
        ),
        task_type="build",
        priority="high",
        depends_on="wfs-schema-01",
    ),
    _task(
        "wne-meta-02",
        "Create args/workflow_templates/ai_ml_transformation.yaml — 5-phase defense contractor AI/ML transformation",
        (
            "New file args/workflow_templates/ai_ml_transformation.yaml. "
            "Include narrative_context block (audience: leadership, org_name: 'Company A', "
            "program_name: 'AI Enablement Program', timeframe_months: 18, "
            "parameters: workforce_size=120, developers_targeted=45, "
            "avg_annual_salary_usd=130000, contract_value_usd=12000000, "
            "ai_productivity_gain_pct=30, training_cost_per_person_usd=8500, "
            "lab_standup_cost_usd=250000, free_resources_budget_usd=0, "
            "paid_courses_per_person_usd=1200, ilt_cost_per_person_usd=3500). "
            ""
            "5 phases, all 4 node types (tool/human/approval/composite): "
            ""
            "Phase 1 — Assessment & Sponsorship: "
            "  skill_inventory (tool) → gap_analysis (tool) → roi_model (tool) "
            "  → coa_development (composite: coa_a organic, coa_b hybrid, coa_c sprint) "
            "  → leadership_brief (human: program_director) "
            "  → funding_approval (approval: all, role: executive_sponsor) "
            ""
            "Phase 2 — Foundation (parallel after funding_approval): "
            "  lab_standup (composite: infra_provision tool, toolchain_select human:chief_architect, "
            "    sandbox_env tool depends_on infra+toolchain) "
            "  curriculum_design (human: learning_development_lead, depends_on: gap_analysis+funding_approval) "
            ""
            "Phase 3 — Workforce Development (depends_on: lab_standup + curriculum_design): "
            "  workforce_dev (composite): "
            "    track_awareness (tool) — 4h exec/PM modules "
            "    track_online_free (tool) — AWS SkillBuilder free, DeepLearning.ai free audit, "
            "      fast.ai, Hugging Face NLP Course, Google ML Crash Course "
            "    track_online_paid (tool) — Coursera Deep Learning Specialization, "
            "      AWS SkillBuilder ML Specialty path, Udemy LangChain, Pluralsight enterprise "
            "    track_instructor_led (human: learning_development_lead) — "
            "      AWS SageMaker 3-day ILT, Azure AI Engineer bootcamp, "
            "      Internal ICDEV AI Enablement bootcamp 5-day, Anthropic prompt engineering 1-day "
            "    track_ojt_tinkering (human: technical_lead, human_required: true) — "
            "      Sprinkle AI into existing product, 2-day hackathon, pair programming, "
            "      10pct dedicated AI exploration time "
            "    ojt_integration (human: technical_lead, depends_on: track_online_free+track_instructor_led) "
            "  competency_assessment (tool, depends_on: workforce_dev) "
            ""
            "Phase 4 — Contract Integration (depends_on: competency_assessment): "
            "  ai_feature_id (tool) "
            "  → mvp_sprints (composite: mvp_reporting_ai, mvp_intelligent_search, mvp_agentic_automation) "
            "  → contract_mod_approval (approval: all, role: contracting_officer) "
            ""
            "Phase 5 — Scale & Measure (depends_on: contract_mod_approval): "
            "  roi_measurement (tool) → scale_plan (human: program_director) "
            "  → ao_brief (approval: any_one, role: authorizing_official) "
            ""
            "Run python tools/studio/template_linter.py --check on the file after — 0 errors required."
        ),
        task_type="build",
        priority="high",
        depends_on="wne-meta-01",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC CORE — Engine modules
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wne-core-01",
        "Create tools/studio/wne/__init__.py + context_builder.py — DAG traversal to WorkflowContext",
        (
            "New directory tools/studio/wne/. "
            "Create tools/studio/wne/__init__.py (empty). "
            "Create tools/studio/wne/context_builder.py: "
            "  WorkflowContext dataclass: "
            "    template_name: str, audience: str, org_name: str, program_name: str, "
            "    classification: str, purpose: str, timeframe_months: int, "
            "    parameters: dict, phases: list[Phase], decision_points: list[DecisionPoint], "
            "    approval_gates: list[ApprovalGate] "
            "  Phase dataclass: name, nodes: list[str], phase_type: str "
            "  DecisionPoint dataclass: node_id, name, role, doc_template "
            "  ApprovalGate dataclass: node_id, name, policy, role "
            "  WorkflowContextBuilder class with build(yaml_path_or_dict) -> WorkflowContext: "
            "    1. Parse YAML (support both file path and dict) "
            "    2. Extract narrative_context — degrade gracefully if missing "
            "    3. Topological sort (Kahn's algorithm) on steps "
            "    4. Group sorted steps into phases (new phase when a step has multiple depends_on "
            "       from different prior phases, or is an approval/human gate) "
            "    5. Collect decision_points (node_type: human) "
            "    6. Collect approval_gates (node_type: approval) "
            "  CLI: --build <yaml_path> --json (prints WorkflowContext as JSON) "
            "No LLM. No DB. Air-gap safe. Only touch wne/__init__.py and wne/context_builder.py."
        ),
        task_type="build",
        priority="critical",
        depends_on="wne-meta-01",
    ),
    _task(
        "wne-core-02",
        "Create tools/studio/wne/narrative_generator.py — audience-driven Jinja2 + LLM prose",
        (
            "New file tools/studio/wne/narrative_generator.py. "
            "NarrativeGenerator class with generate(ctx: WorkflowContext) -> NarrativeResult: "
            "  NarrativeResult dataclass: executive_summary, phase_narratives (list), "
            "    decision_point_rationale, risk_summary, slide_bullets (list of 10) "
            "Pattern: replicate tools/compliance/narrative_generator.py approach: "
            "  1. Load Jinja2 template from hardprompts/wne/exec_brief.md "
            "  2. Render template with WorkflowContext fields (deterministic) "
            "  3. If LLM available (LLMRouter().has_any_llm()): refine rendered prose "
            "  4. Return result (Jinja2-only output if no LLM — never fail) "
            "Audience routing in template: "
            "  leadership: lead with COA + ROI + funding ask "
            "  compliance: lead with control coverage + risk reduction "
            "  technical: lead with toolchain + integration steps "
            "  board: lead with business value + competitive positioning "
            "Also create hardprompts/wne/exec_brief.md — Jinja2 template with "
            "  {% if audience == 'leadership' %} sections for COA/ROI first, etc. "
            "And hardprompts/wne/slide_outline.md — 10-slide structure derived from phases. "
            "Use LLMRouter for 'narrative_generation' function. Air-gap safe. "
            "Only touch narrative_generator.py, exec_brief.md, slide_outline.md."
        ),
        task_type="build",
        priority="critical",
        depends_on="wne-core-01",
    ),
    _task(
        "wne-core-03",
        "Create tools/studio/wne/coa_builder.py — 3-COA comparison from composite nodes or parametric fallback",
        (
            "New file tools/studio/wne/coa_builder.py. "
            "COABuilder class with build(ctx: WorkflowContext) -> COAResult: "
            "  COAResult dataclass: coa_a, coa_b, coa_c (each: COAOption) "
            "  COAOption dataclass: name, approach, timeline_months, cost_usd, "
            "    risk_level (low|medium|high), recommendation (bool), rationale "
            ""
            "Primary path — extract from composite nodes: "
            "  Find any composite node whose id or sub_steps contain 'coa_a', 'coa_b', 'coa_c'. "
            "  Use those sub_step names + descriptions as the option labels. "
            ""
            "Fallback path (no coa_* nodes found) — parametric generation: "
            "  Reuse hardprompts/simulation/coa_generation.md structure. "
            "  COA A (Speed/Organic): training only, no lab, 1.5x timeframe, 0.4x cost "
            "  COA B (Balanced — recommended): lab + training + OJT, 1.0x, 1.0x "
            "  COA C (Comprehensive/Sprint): all tracks parallel, 0.7x time, 2.2x cost "
            "  Costs derived from ctx.parameters. "
            ""
            "Also create hardprompts/wne/coa_comparison.md — markdown table template. "
            "CLI: --build <yaml_path> --json "
            "No LLM required. Only touch coa_builder.py and coa_comparison.md."
        ),
        task_type="build",
        priority="high",
        depends_on="wne-core-01",
    ),
    _task(
        "wne-core-04",
        "Create tools/studio/wne/roi_calculator.py + budget_estimator.py",
        (
            "New file tools/studio/wne/roi_calculator.py: "
            "ROICalculator class with calculate(ctx: WorkflowContext) -> ROIResult: "
            "  ROIResult: total_investment_usd, total_value_3yr_usd, roi_pct, "
            "    payback_months, npv_usd, sensitivity_table (list of dicts) "
            "Formula (from ctx.parameters): "
            "  training_cost = developers_targeted * training_cost_per_person_usd "
            "  total_investment = lab_standup_cost_usd + training_cost "
            "  annual_value = workforce_size * avg_annual_salary_usd * (ai_productivity_gain_pct/100) "
            "  value_3yr = annual_value * (timeframe_months/12) "
            "  roi_pct = (value_3yr - total_investment) / total_investment * 100 "
            "  npv = NPV at 8% discount over timeframe_months "
            "  payback_months = total_investment / (annual_value / 12) "
            "  sensitivity_table: vary productivity_gain_pct ±10pp in 5pp steps "
            "Degrade gracefully: if parameters missing, return roi_pct=None with note. "
            ""
            "New file tools/studio/wne/budget_estimator.py: "
            "BudgetEstimator class with estimate(ctx: WorkflowContext) -> BudgetResult: "
            "  BudgetResult: phases (list of PhaseCost), total_usd, by_type (tool/human/approval) "
            "  PhaseCost: phase_name, cost_usd, node_breakdown (list) "
            "Node cost heuristics from ctx.parameters: "
            "  tool node: $0 (automated — already built in system) "
            "  human node: role_days * (avg_salary / 260) "
            "  approval node: 0.5 FTE-days * avg_salary/260 (overhead) "
            "  composite: sum of sub_steps "
            "Reuse tools/simulation/simulation_engine.py cost dimension T-shirt mapping. "
            "Only touch roi_calculator.py and budget_estimator.py."
        ),
        task_type="build",
        priority="high",
        depends_on="wne-core-01",
    ),
    _task(
        "wne-core-05",
        "Create tools/studio/wne/export_pack_generator.py — orchestrate all modules → zip bundle",
        (
            "New file tools/studio/wne/export_pack_generator.py. "
            "ExportPackGenerator class with generate(yaml_path, output_dir, audience=None) -> Path: "
            "  1. WorkflowContextBuilder.build(yaml_path) → ctx "
            "  2. Override ctx.audience with audience arg if provided "
            "  3. NarrativeGenerator.generate(ctx) → narrative "
            "  4. COABuilder.build(ctx) → coa "
            "  5. ROICalculator.calculate(ctx) → roi "
            "  6. BudgetEstimator.estimate(ctx) → budget "
            "  7. Write 6 files to output_dir: "
            "     exec_brief.md     — narrative.executive_summary + phase_narratives "
            "     coa_comparison.md — COA table (reuse coa_comparison.md template) "
            "     budget_table.md   — budget.phases as markdown table "
            "     roi_analysis.md   — roi fields + sensitivity table "
            "     slide_outline.md  — narrative.slide_bullets formatted as slide notes "
            "     workflow_summary.json — {template_name, audience, node_count, phases, "
            "                             total_investment_usd, roi_pct, generated_at} "
            "  8. Zip all 6 files → <program_name>_narrative_pack.zip "
            "  9. Return zip path "
            "Reuse tools/canvas/export_utils.py for markdown formatting helpers. "
            "CLI: --template <yaml_path> --output <dir> --audience leadership|technical|... "
            "All failures are non-fatal (write empty section with error note). "
            "Only touch export_pack_generator.py."
        ),
        task_type="build",
        priority="critical",
        depends_on="wne-core-04",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC CHAT — /studio/narrate end-user interface
    # End users have NO Claude CLI access — all interaction via this page
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wne-chat-01",
        "DB migration: wne_sessions + wne_artifacts tables",
        (
            "Create tools/db/migrations/0XX_wne_sessions/up.py "
            "(use next available migration number after current max). "
            "Table wne_sessions: "
            "  id TEXT PRIMARY KEY, workflow_id TEXT, template_slug TEXT, "
            "  status TEXT CHECK(status IN ('collecting','confirming','generating','reviewing','done','failed')), "
            "  context_json TEXT, chat_context_id TEXT, "
            "  org_name TEXT, audience TEXT, program_name TEXT, "
            "  created_at TEXT NOT NULL, updated_at TEXT NOT NULL "
            "Table wne_artifacts: "
            "  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES wne_sessions(id), "
            "  artifact_type TEXT CHECK(artifact_type IN "
            "    ('exec_brief','coa_comparison','budget_table','roi_analysis','slide_outline','zip_bundle')), "
            "  content TEXT, generated_at TEXT NOT NULL "
            "Indices: idx_wne_sessions_status, idx_wne_artifacts_session_id. "
            "Add wne_artifacts to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. "
            "Add both tables to MINIMAL_ICDEV_SCHEMA in tests/conftest.py. "
            "Only touch migration up.py, pre_tool_use.py, conftest.py."
        ),
        task_type="build",
        priority="critical",
        depends_on="wne-core-05",
    ),
    _task(
        "wne-chat-02",
        "Create tools/studio/wne/chat_engine.py — session state machine + narrative_context extraction",
        (
            "New file tools/studio/wne/chat_engine.py. "
            "WNEChatEngine class wrapping tools/dashboard/chat_manager.py: "
            ""
            "State machine: COLLECTING → CONFIRMING → GENERATING → REVIEWING → DONE "
            ""
            "create_session(workflow_id_or_template): "
            "  - Load workflow YAML, extract any existing narrative_context "
            "  - INSERT wne_sessions row (status='collecting') "
            "  - Create chat context via chat_manager.create_context() "
            "  - Return session_id "
            ""
            "send_message(session_id, user_message): "
            "  - If status='collecting': "
            "      Extract narrative_context fields from message using field detection "
            "      (e.g. if message answers 'who is this for' → audience field) "
            "      Update context_json in wne_sessions "
            "      Check if all required fields filled (audience, org_name, purpose) "
            "      If complete → transition to 'confirming', reply with summary + 'Shall I generate?' "
            "      Else → reply with next question "
            "  - If status='confirming' and user says yes/proceed: "
            "      Transition to 'generating' "
            "      Call ExportPackGenerator.generate() → zip "
            "      Store each artifact in wne_artifacts "
            "      Transition to 'reviewing', return preview of exec_brief first 500 chars "
            "  - If status='reviewing': "
            "      User can ask refinements — re-run narrative_generator with instruction "
            "      User says 'export' → return download token "
            ""
            "Required narrative_context fields: audience, org_name, purpose "
            "All other fields have defaults. "
            "Only touch chat_engine.py."
        ),
        task_type="build",
        priority="critical",
        depends_on="wne-chat-01",
    ),
    _task(
        "wne-chat-03",
        "Create tools/dashboard/templates/studio/narrate.html — /studio/narrate chat UI",
        (
            "New file tools/dashboard/templates/studio/narrate.html. "
            "Adapt the /simulate/chat UI pattern from tools/simulation/blueprint.py. "
            "Layout: two-panel "
            "  Left panel (60%): chat conversation (user messages + WNE responses) "
            "  Right panel (40%): live preview pane "
            "    - Shows 'Waiting for context...' until status=reviewing "
            "    - When status=reviewing: renders exec_brief.md preview (first section) "
            "    - Tabs for each artifact: Brief | COA | Budget | ROI | Slides "
            "    - Export button (bottom): 'Download Full Pack (.zip)' "
            "Header: workflow name breadcrumb + status badge (Collecting/Confirming/Generating/Reviewing) "
            "Message input: textarea + Send button (Enter to send) "
            "Auto-scroll on new message. "
            "JS: "
            "  POST /api/wne/session on page load if no session_id in URL "
            "  POST /api/wne/<session_id>/message on send "
            "  GET /api/wne/<session_id>/artifact?type=exec_brief for preview "
            "  Poll GET /api/wne/<session_id>/status during generating state (2s interval) "
            "  GET /api/wne/<session_id>/artifact?type=zip_bundle for download "
            "Mirror to icdev/tools/dashboard/templates/studio/narrate.html. "
            "Only touch narrate.html (both copies)."
        ),
        task_type="build",
        priority="high",
        depends_on="wne-chat-02",
    ),
    _task(
        "wne-chat-04",
        "Add WNE API routes to tools/studio/studio.py",
        (
            "In tools/studio/studio.py add 4 new routes: "
            ""
            "@bp.route('/api/wne/session', methods=['POST']) "
            "def wne_create_session(): "
            "  body: {workflow_id: str or template_slug: str} "
            "  calls WNEChatEngine.create_session() "
            "  returns: {session_id, status, first_question} "
            ""
            "@bp.route('/api/wne/<session_id>/message', methods=['POST']) "
            "def wne_send_message(session_id): "
            "  body: {message: str} "
            "  calls WNEChatEngine.send_message() "
            "  returns: {reply, status, context_progress: {filled, total}} "
            ""
            "@bp.route('/api/wne/<session_id>/artifact') "
            "def wne_get_artifact(session_id): "
            "  query param: type (exec_brief|coa_comparison|budget_table|roi_analysis|slide_outline|zip_bundle) "
            "  returns artifact content (zip as binary download, others as text/markdown) "
            ""
            "@bp.route('/api/wne/<session_id>/status') "
            "def wne_get_status(session_id): "
            "  returns: {status, context_json, artifacts_ready: list} "
            ""
            "Also add page route: "
            "@bp.route('/studio/narrate') "
            "def studio_narrate(): renders narrate.html "
            ""
            "Use get_connection() throughout. Wrap all in try/except with 500 fallback. "
            "Only touch studio.py."
        ),
        task_type="build",
        priority="high",
        depends_on="wne-chat-03",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC API — Studio toolbar button
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wne-api-01",
        "Studio toolbar: add Generate Narrative Pack button → /studio/narrate",
        (
            "In tools/dashboard/templates/studio/workflow_studio.html: "
            "Find the existing toolbar area with the Export YAML button. "
            "Add a split-button pattern next to it: "
            "  Primary: 'Generate Narrative Pack' (📦) "
            "    onclick: if workflow is saved → window.location = '/studio/narrate?workflow_id=' + currentWorkflowId "
            "             if unsaved → toast('Save workflow first, then click Generate Narrative Pack') "
            "  Secondary dropdown: 'Export YAML' (existing functionality) "
            ""
            "Also add an 'Open Narrative' link on each saved-workflow card in the Saved tab: "
            "  Small link: 'Generate Brief' → /studio/narrate?workflow_id=<id> "
            ""
            "Mirror template changes to icdev/tools/dashboard/templates/studio/workflow_studio.html. "
            "Only touch workflow_studio.html (both copies)."
        ),
        task_type="build",
        priority="medium",
        depends_on="wne-chat-04",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC VV — V&V Gate
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wne-vv-01",
        "Unit tests: context_builder, roi_calculator, coa_builder, export_pack",
        (
            "Create tests/studio/test_wne_context_builder.py — 5 tests: "
            "  (1) build() on ai_ml_transformation.yaml → phases >= 5 "
            "  (2) decision_points extracted (human nodes) "
            "  (3) approval_gates extracted "
            "  (4) degrade gracefully when narrative_context missing "
            "  (5) topological sort respects depends_on order "
            ""
            "Create tests/studio/test_wne_roi_calculator.py — 4 tests: "
            "  (1) NPV formula at 8% discount, payback < timeframe_months "
            "  (2) roi_pct > 0 for standard parameters "
            "  (3) sensitivity table has 5 rows "
            "  (4) degrade gracefully when parameters empty "
            ""
            "Create tests/studio/test_wne_coa_builder.py — 3 tests: "
            "  (1) parametric fallback always returns 3 COAs "
            "  (2) COA B is recommended=True "
            "  (3) composite coa_* node extraction works when present "
            ""
            "Create tests/studio/test_wne_export_pack.py — 3 tests: "
            "  (1) generate() produces zip with 6 files "
            "  (2) exec_brief.md non-empty "
            "  (3) workflow_summary.json is valid JSON with roi_pct key "
            ""
            "All tests use tmp_path, no DB, no LLM (air-gap safe). "
            "Run: pytest tests/studio/test_wne_*.py -v"
        ),
        task_type="test",
        priority="high",
        depends_on="wne-core-05",
    ),
    _task(
        "wne-vv-02",
        "V&V gate — E2E: ai_ml_transformation template → chat → generate → export zip",
        (
            "1. python tools/studio/template_linter.py --check "
            "   args/workflow_templates/ai_ml_transformation.yaml "
            "   (0 errors required) "
            ""
            "2. CLI smoke test: "
            "   python tools/studio/wne/export_pack_generator.py "
            "     --template args/workflow_templates/ai_ml_transformation.yaml "
            "     --output .tmp/wne_vv/ --audience leadership "
            "   Verify .tmp/wne_vv/ contains 6 files + zip "
            "   Verify exec_brief.md contains 'COA B' or 'Hybrid' "
            "   Verify roi_analysis.md contains a numeric roi_pct "
            ""
            "3. Run DB migration: python tools/db/migrate.py --up "
            "   Verify wne_sessions and wne_artifacts tables exist "
            ""
            "4. pytest tests/studio/test_wne_*.py -v (all tests pass) "
            ""
            "5. Browser/Playwright at http://localhost:5050/studio/workflows: "
            "   - Load ai_ml_transformation template "
            "   - Click 'Generate Narrative Pack' "
            "   - At /studio/narrate: answer 4 questions in chat "
            "   - Confirm generation → verify right panel shows exec_brief preview "
            "   - Click Download Full Pack → verify zip downloads "
            ""
            "6. python tools/workflow/coherence_checker.py --all --gate (clean) "
            "7. python tools/dx/companion.py --sync --write --json (in sync) "
            ""
            "All 7 checks must pass before marking done."
        ),
        task_type="run",
        priority="critical",
        depends_on="wne-vv-01",
    ),
]


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()

    inserted = skipped = 0
    for task in TASKS:
        cur.execute(INSERT_SQL, task)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    meta   = sum(1 for t in TASKS if t[0].startswith("wne-meta"))
    core   = sum(1 for t in TASKS if t[0].startswith("wne-core"))
    chat   = sum(1 for t in TASKS if t[0].startswith("wne-chat"))
    api    = sum(1 for t in TASKS if t[0].startswith("wne-api"))
    vv     = sum(1 for t in TASKS if t[0].startswith("wne-vv"))

    print(f"[seed_wne_plan] done — {inserted} inserted, {skipped} skipped (conflict)")
    print(f"  Epic meta  (schema + template):       wne-meta-01..02   ({meta} tasks)")
    print(f"  Epic core  (engine modules):          wne-core-01..05   ({core} tasks)")
    print(f"  Epic chat  (chat interface + API):    wne-chat-01..04   ({chat} tasks)")
    print(f"  Epic api   (Studio toolbar button):   wne-api-01        ({api} task)")
    print(f"  Epic vv    (unit tests + E2E):        wne-vv-01..02     ({vv} tasks)")
    print()
    print(f"  Total: {len(TASKS)} tasks queued")
    print()
    print("Dependency chain:")
    print("  wfs-schema-01 -> wne-meta-01 -> wne-meta-02")
    print("  wne-meta-01   -> wne-core-01 -> wne-core-02/03/04 -> wne-core-05")
    print("  wne-core-05   -> wne-chat-01 -> wne-chat-02 -> wne-chat-03 -> wne-chat-04")
    print("  wne-chat-04   -> wne-api-01")
    print("  wne-core-05   -> wne-vv-01 -> wne-vv-02")
    print()
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
