# CUI // SP-CTI
"""Seed script — AI Skills Gap Enablement Platform (aisg-).

Strategic initiative to position ICDEV as the vehicle that closes the AI
skills gap for government agencies and commercial companies. Four phases:
  Phase A: Zero-to-AI in One Day (wizard, role views, explain-this)
  Phase B: Guided AI Development Curriculum (pattern library, learning paths, memory handoff)
  Phase C: No-Code AI Customization (visual builder, autotune fine-tuning, graduated trust)
  Phase D: AI Workforce Multiplier Metrics (ROI dashboard, skills gap tracker, talent retention)

Run:
    cd /path/to/ICDev && PYTHONPATH=. python tools/kanban/seed_aisg.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

TASKS: list[dict] = [

    # ── PHASE A-1: Launchpad Wizard ──────────────────────────────────────────

    {
        "id": "aisg-a1-01",
        "title": "DB migration — aisg_wizard_sessions table",
        "description": (
            "Create migration file tools/db/migrations/084_aisg_wizard.sql. "
            "Table: aisg_wizard_sessions (id SERIAL PK, session_token TEXT UNIQUE, "
            "use_case TEXT, compliance_level TEXT, tech_stack TEXT, "
            "ai_maturity TEXT CHECK (ai_maturity IN ('none','pilot','scaling')), "
            "cloud_provider TEXT, generated_args_json JSONB, "
            "created_at TIMESTAMPTZ DEFAULT NOW()). "
            "Add graceful table-existence guard in the Python migration runner. "
            "No other files changed."
        ),
        "task_type": "chore",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-a1-02",
        "title": "Build tools/aisg/wizard.py — 5-question setup wizard backend",
        "description": (
            "New file tools/aisg/wizard.py. "
            "Class WizardEngine with method process(session_token, answers: dict) -> dict. "
            "Answers keys: use_case, compliance_level (IL2/IL4/IL5/FedRAMP/CMMC), "
            "tech_stack, ai_maturity (none/pilot/scaling), cloud_provider. "
            "Output: write pre-configured args/ overrides to aisg_wizard_sessions.generated_args_json, "
            "return dict with recommended_goals (list of goal filenames), "
            "recommended_skills (list of skill names), sprint_plan_tasks (list of task dicts). "
            "Reuse get_connection(). No LLM call — pure rule-based mapping. "
            "Add tools/aisg/__init__.py."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a1-01",
    },
    {
        "id": "aisg-a1-03",
        "title": "Build wizard HTML template + blueprint route /ai-wizard",
        "description": (
            "New template tools/dashboard/templates/aisg/wizard.html. "
            "5-step form: (1) use_case textarea, (2) compliance_level select, "
            "(3) tech_stack checkboxes, (4) ai_maturity radio, (5) cloud_provider select. "
            "Submit POSTs to /ai-wizard/submit; response renders recommended goals + skills "
            "in a results card. "
            "Add @bp.route('/ai-wizard') GET and /ai-wizard/submit POST to "
            "tools/aisg/blueprint.py (new file). "
            "Follow 7-component page completeness rule: "
            "template, icdev/ mirror, route, backing module, constants, migration (done), nav link. "
            "Add /ai-wizard to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a1-02",
    },
    {
        "id": "aisg-a1-04",
        "title": "Auto-seed 30-day Sprint Kanban from wizard maturity level",
        "description": (
            "Extend tools/aisg/wizard.py: after saving session, call "
            "sprint_seeder.seed_for_maturity(maturity, session_token) which inserts "
            "~10 Kanban tasks tagged with dispatch_source='aisg-wizard'. "
            "For maturity='none': seed Consumer track tasks (run /icdev-build, explore /ask-icdev, "
            "review first pattern deploy). "
            "For maturity='pilot': seed Configurator track tasks (tune args/llm_config.yaml, "
            "deploy first RAG pipeline, run /icdev-comply). "
            "For maturity='scaling': seed Builder track tasks (build custom agent, fine-tune model, "
            "configure Genesis advisor mode). "
            "New file tools/aisg/sprint_seeder.py. Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a1-03",
    },
    {
        "id": "aisg-a1-05",
        "title": "V&V — wizard roundtrip test + page render",
        "description": (
            "Gate task only — no code changes. "
            "Run: pytest tests/ -k aisg_wizard -v. "
            "Verify: (1) wizard.process() returns recommended_goals for each maturity level; "
            "(2) session saved to aisg_wizard_sessions; "
            "(3) sprint_seeder inserts correct task count per maturity; "
            "(4) /ai-wizard page renders 200 via tools/testing/health_check.py route probe. "
            "Fix any failures before marking done."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a1-04",
    },

    # ── PHASE A-2: Role-Based Dashboard Views ────────────────────────────────

    {
        "id": "aisg-a2-01",
        "title": "Add role selector to dashboard auth + role field to user session",
        "description": (
            "Extend dashboard login to include a role dropdown: "
            "Builder / Compliance Officer / Executive (CIO) / Product Manager. "
            "Store selected role in the Flask session as session['dashboard_role']. "
            "Edit tools/dashboard/blueprint.py login route and login.html template. "
            "Add a role switcher widget to base.html nav bar (visible when logged in). "
            "No new DB table needed — role lives in the session cookie."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a1-05",
    },
    {
        "id": "aisg-a2-02",
        "title": "Build Compliance Officer role view template (/dashboard/compliance-view)",
        "description": (
            "New template tools/dashboard/templates/aisg/compliance_view.html. "
            "Sections: ATO status summary (control families: green/yellow/red), "
            "open POAM items count, SBOM freshness date, last STIG scan result, "
            "evidence collection status. "
            "Pull data from existing compliance tables via a new compliance_view.py helper. "
            "Add @bp.route('/dashboard/compliance-view') to tools/aisg/blueprint.py. "
            "Only show this view when session['dashboard_role'] == 'compliance'."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a2-01",
    },
    {
        "id": "aisg-a2-03",
        "title": "Build CIO/Executive role view template (/dashboard/executive-view)",
        "description": (
            "New template tools/dashboard/templates/aisg/executive_view.html. "
            "Sections: AI investment ROI summary card (hours saved this week), "
            "agent activity digest (top 5 actions taken by AI), "
            "cost by function (LLM API spend breakdown), "
            "maturity trend chart (30-day moving average of AI maturity score). "
            "Pull from aisg_roi_events and aisg_skill_usage tables (created in Phase D). "
            "Use placeholder data if those tables don't exist yet (graceful fallback). "
            "Add @bp.route('/dashboard/executive-view') to tools/aisg/blueprint.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a2-01",
    },
    {
        "id": "aisg-a2-04",
        "title": "Build Product Manager role view template (/dashboard/pm-view)",
        "description": (
            "New template tools/dashboard/templates/aisg/pm_view.html. "
            "Sections: feature velocity (stories done this sprint), "
            "test coverage percentage, active Kanban sprint tasks, "
            "next learning path milestone for the team. "
            "Pull from kanban_tasks (status=done, sprint filter) and "
            "aisg_track_progress (created in Phase B). "
            "Graceful fallback if aisg_track_progress doesn't exist. "
            "Add @bp.route('/dashboard/pm-view') to tools/aisg/blueprint.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a2-01",
    },
    {
        "id": "aisg-a2-05",
        "title": "Add role-based auto-redirect on dashboard home + view switcher nav",
        "description": (
            "Edit tools/dashboard/blueprint.py index route: after login, if "
            "session['dashboard_role'] is set, redirect to the matching view URL. "
            "Add a 'Switch View' dropdown to base.html nav bar that lists all 4 roles "
            "and allows switching without re-login. "
            "Depends on a2-02, a2-03, a2-04 all being done."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a2-04",
    },

    # ── PHASE A-3: Explain This Decision ─────────────────────────────────────

    {
        "id": "aisg-a3-01",
        "title": "Add ExplainPanel component to base.html (collapsible slide-out)",
        "description": (
            "Edit tools/dashboard/templates/base.html: add a hidden side panel div "
            "#explain-panel with a close button and content placeholder. "
            "Add JavaScript: window.showExplain(eventId) fetches /api/explain/<event_id> "
            "and renders the response into #explain-panel, then slides it open. "
            "Add a small '? Explain' button style to the shared CSS. "
            "No backend logic in this task — JS + HTML only."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-a3-02",
        "title": "Build tools/aisg/explain_translator.py — AI decision to plain-English",
        "description": (
            "New file tools/aisg/explain_translator.py. "
            "Function translate(event_type: str, event_data: dict) -> dict with keys: "
            "headline (1 sentence), why_it_matters (1 sentence), confidence_label "
            "('High'/'Medium'/'Low' mapped from 0.0-1.0), alternative_considered (optional). "
            "Implement rule-based translation for event types: "
            "self_heal_action, readiness_score_update, pattern_recommendation, "
            "gap_detection, genesis_reflex_fired. "
            "No LLM call — deterministic string templates with event_data substitution. "
            "Add /api/explain/<event_id> GET endpoint to tools/aisg/blueprint.py that "
            "fetches the event from the audit log and returns translate() output as JSON."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-a3-03",
        "title": "Wire Explain buttons to decision audit log entries in key dashboard pages",
        "description": (
            "Add showExplain(eventId) call buttons to three existing dashboard pages: "
            "(1) Self-heal action list in monitoring page, "
            "(2) Requirements readiness score card in intake page, "
            "(3) Genesis reflex activity feed. "
            "Each button triggers the ExplainPanel via the JS function built in a3-01. "
            "Edit only the three template files — no backend changes."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-a3-02",
    },

    # ── PHASE B-1: AI Pattern Library ────────────────────────────────────────

    {
        "id": "aisg-b1-01",
        "title": "DB migration — aisg_patterns table",
        "description": (
            "Add to migration 084 (or create 085 if 084 already ran): "
            "Table aisg_patterns (id TEXT PK, name TEXT NOT NULL, category TEXT, "
            "description TEXT, use_case TEXT, deploy_config JSONB, "
            "created_at TIMESTAMPTZ DEFAULT NOW()). "
            "Category CHECK constraint values: 'document_qa', 'procurement', "
            "'threat_triage', 'compliance_evidence', 'custom'. "
            "No other changes."
        ),
        "task_type": "chore",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-b1-02",
        "title": "Build tools/aisg/pattern_registry.py — pattern CRUD + deploy handler",
        "description": (
            "New file tools/aisg/pattern_registry.py. "
            "Functions: list_patterns() -> list[dict], get_pattern(id) -> dict, "
            "deploy_pattern(id, target_dir: Path) -> dict with status and file list. "
            "deploy_pattern copies the pattern's deploy_config template files to target_dir "
            "and registers a new Kanban task to implement them. "
            "Uses get_connection(). Seed the 4 built-in patterns on first call if table empty."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b1-01",
    },
    {
        "id": "aisg-b1-03",
        "title": "Build Document Q&A Agent pattern entry + deploy template",
        "description": (
            "Add to pattern_registry.py seed data: id='document-qa', "
            "name='Document Q&A Agent', category='document_qa'. "
            "deploy_config specifies: goals/document_qa.md template, "
            "tools/rag/ as the backing RAG pipeline, "
            "args/rag_config.yaml source settings. "
            "Create the goals/document_qa.md template file with the 5-step FORGE workflow "
            "for ingesting PDFs/SharePoint docs and answering natural language queries. "
            "Pattern deploys with one command: /icdev-pattern deploy document-qa."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b1-02",
    },
    {
        "id": "aisg-b1-04",
        "title": "Build Procurement Intel Agent pattern entry + deploy template",
        "description": (
            "Add pattern: id='procurement-intel', name='Procurement Intel Agent', "
            "category='procurement'. "
            "deploy_config: reuses tools/govcon/ SAM.gov scanner + competitive intel pipeline. "
            "Create goals/procurement_intel.md template: 4-step workflow "
            "(scan SAM.gov opportunities, extract requirements, map to capabilities, "
            "draft response outline). "
            "No new Python modules — compose existing govcon tools."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b1-02",
    },
    {
        "id": "aisg-b1-05",
        "title": "Build Threat Triage Agent pattern entry + deploy template",
        "description": (
            "Add pattern: id='threat-triage', name='Threat Triage Agent', "
            "category='threat_triage'. "
            "deploy_config: reuses NVD/CISA feed ingestion from tools/security/ "
            "and the knowledge base pattern matcher from tools/knowledge/. "
            "Create goals/threat_triage.md template: ingest CVE feed, correlate with "
            "SIEM events, classify by severity and asset impact, produce triage report. "
            "No new Python — compose existing security + monitoring tools."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b1-02",
    },
    {
        "id": "aisg-b1-06",
        "title": "Build Compliance Evidence Agent pattern entry + deploy template",
        "description": (
            "Add pattern: id='compliance-evidence', name='Compliance Evidence Agent', "
            "category='compliance_evidence'. "
            "deploy_config: reuses tools/compliance/ evidence collector and SSP generator. "
            "Create goals/compliance_evidence.md template: schedule evidence collection "
            "on cron, map artifacts to NIST controls, update POAM status, "
            "generate progress report. "
            "No new Python — compose existing compliance tools."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b1-02",
    },
    {
        "id": "aisg-b1-07",
        "title": "Build /icdev-pattern CLI command + pattern browser dashboard page",
        "description": (
            "New CLI entry: python tools/aisg/pattern_registry.py --list / --deploy <id>. "
            "New dashboard page: tools/dashboard/templates/aisg/patterns.html. "
            "Shows pattern cards (name, category badge, use_case, Deploy button). "
            "Deploy button POSTs to /api/pattern/deploy and shows confirmation with "
            "generated task ID. "
            "Add @bp.route('/ai-patterns') GET to tools/aisg/blueprint.py. "
            "Add /ai-patterns to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b1-06",
    },

    # ── PHASE B-2: In-Platform Learning Paths ────────────────────────────────

    {
        "id": "aisg-b2-01",
        "title": "DB migration — aisg_learning_tracks + aisg_track_progress tables",
        "description": (
            "Add to migration 084/085: "
            "aisg_learning_tracks (id TEXT PK, name TEXT, level TEXT "
            "CHECK (level IN ('consumer','configurator','builder')), "
            "description TEXT, task_count INT). "
            "aisg_track_progress (id SERIAL PK, user_email TEXT, track_id TEXT "
            "REFERENCES aisg_learning_tracks(id), tasks_completed INT DEFAULT 0, "
            "activated_at TIMESTAMPTZ, completed_at TIMESTAMPTZ). "
            "Seed 3 built-in tracks on first run."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-b2-02",
        "title": "Build tools/aisg/learning_paths.py — 3-track definitions + progress tracker",
        "description": (
            "New file tools/aisg/learning_paths.py. "
            "Seed data for 3 tracks: "
            "Consumer (run /icdev-build, explore /ask-icdev, use first pattern, review explain panel), "
            "Configurator (tune llm_config.yaml, deploy RAG pipeline, run /icdev-comply, tweak args/), "
            "Builder (build custom agent, fine-tune model, configure Genesis graduated trust, deploy to IL4). "
            "Functions: get_tracks(), activate_track(user_email, track_id), "
            "update_progress(user_email, track_id, tasks_completed). "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b2-01",
    },
    {
        "id": "aisg-b2-03",
        "title": "Build learning path dashboard page /ai-learning + progress UI",
        "description": (
            "New template tools/dashboard/templates/aisg/learning.html. "
            "Shows 3 track cards (Consumer/Configurator/Builder) with: "
            "description, task count, progress bar if active, 'Start Track' button. "
            "Active track shows checklist of milestone tasks with completion checkboxes. "
            "Add @bp.route('/ai-learning') GET to tools/aisg/blueprint.py. "
            "Add /ai-learning to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b2-02",
    },
    {
        "id": "aisg-b2-04",
        "title": "Auto-seed Kanban tasks when a learning track is activated",
        "description": (
            "Extend learning_paths.py activate_track(): after saving aisg_track_progress, "
            "call sprint_seeder.seed_for_track(track_id, user_email) which inserts "
            "the track's milestone tasks into kanban_tasks with "
            "dispatch_source='aisg-learning-<track_id>'. "
            "Tasks are HITL (hitl_stage='review') so they queue for user review "
            "before the scheduler picks them up. "
            "Reuses sprint_seeder.py from aisg-a1-04."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b2-03",
    },

    # ── PHASE B-3: Institutional Memory Capture ───────────────────────────────

    {
        "id": "aisg-b3-01",
        "title": "Build tools/aisg/knowledge_handoff.py — knowledge package extractor",
        "description": (
            "New file tools/aisg/knowledge_handoff.py. "
            "Function export_package(user_email: str, output_path: Path) -> dict. "
            "Extracts: (1) last 90 days of audit log entries authored by user_email "
            "(from the audit trail tables), (2) fine-tuning patterns associated with "
            "user sessions (from fine_tuning_datasets), (3) args/ YAML overrides "
            "committed by the user (from git log --author filter). "
            "Writes a portable ZIP: knowledge_package.zip containing "
            "audit_summary.json, patterns.json, args_snapshot.yaml. "
            "Uses get_connection() + subprocess for git log."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-b3-02",
        "title": "Build tools/aisg/onboarding_import.py — knowledge package importer",
        "description": (
            "New file tools/aisg/onboarding_import.py. "
            "Function import_package(zip_path: Path, new_user_email: str) -> dict with "
            "status, imported_patterns_count, imported_args_overrides_count. "
            "Reads knowledge_package.zip, writes patterns to aisg_patterns table "
            "(category='custom'), merges args_snapshot.yaml into current args/ directory "
            "without overwriting existing keys, logs the import to audit trail. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b3-01",
    },
    {
        "id": "aisg-b3-03",
        "title": "Build offboarding wizard page /ai-handoff on dashboard",
        "description": (
            "New template tools/dashboard/templates/aisg/handoff.html. "
            "Two modes: Offboarding (export package for departing user) and "
            "Onboarding (import package for new hire). "
            "Offboarding: select user email, click Export, downloads knowledge_package.zip. "
            "Onboarding: upload ZIP, enter new user email, click Import, shows import summary. "
            "Add @bp.route('/ai-handoff') GET + POST to tools/aisg/blueprint.py. "
            "Add /ai-handoff to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b3-02",
    },

    # ── PHASE C-1: Visual Agent Builder ──────────────────────────────────────

    {
        "id": "aisg-c1-01",
        "title": "DB migration — aisg_agent_designs table",
        "description": (
            "Add to migration 084/085/086: "
            "aisg_agent_designs (id TEXT PK, name TEXT NOT NULL, "
            "canvas_json JSONB, generated_goal_md TEXT, "
            "trust_tier TEXT CHECK (trust_tier IN ('observer','advisor','executor','full')), "
            "created_by TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), "
            "updated_at TIMESTAMPTZ DEFAULT NOW()). "
            "No other tables."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-c1-02",
        "title": "Build tools/aisg/visual_agent_builder.py — FORGE goal generator from canvas JSON",
        "description": (
            "New file tools/aisg/visual_agent_builder.py. "
            "Function generate_goal(design_id: str) -> str (Markdown). "
            "Reads aisg_agent_designs.canvas_json which is a list of node objects "
            "{id, tool_name, trust_tier, inputs, outputs, next_node_id}. "
            "Topologically sorts nodes and renders a goals/custom_<design_id>.md file "
            "following the FORGE goal template structure: "
            "Goal, Inputs, Steps (one per node), Outputs, Error Handling. "
            "Saves generated Markdown to aisg_agent_designs.generated_goal_md. "
            "Uses get_connection(). No LLM — pure template rendering."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c1-01",
    },
    {
        "id": "aisg-c1-03",
        "title": "Build visual agent builder dashboard page /ai-builder",
        "description": (
            "New template tools/dashboard/templates/aisg/agent_builder.html. "
            "Left panel: tool palette (list of tools from tools/manifest/ shards, "
            "grouped by category). "
            "Main canvas: drag-and-drop SVG area using vanilla JS (no npm). "
            "Each dropped tool becomes a node; arrows connect nodes. "
            "Right panel: node properties (tool_name, trust_tier dropdown, "
            "input/output mapping). "
            "Save button POSTs canvas_json to /api/agent-design/save. "
            "Generate button calls /api/agent-design/<id>/generate and shows "
            "the resulting goal Markdown in a preview panel. "
            "Add @bp.route('/ai-builder') and API routes to tools/aisg/blueprint.py. "
            "Add /ai-builder to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c1-02",
    },
    {
        "id": "aisg-c1-04",
        "title": "Add trust-tier selector and color-coding per node in agent builder UI",
        "description": (
            "Extend agent_builder.html: in the node properties right panel, "
            "add a trust_tier select (Observer=green / Advisor=yellow / "
            "Executor=orange / Full=red). "
            "Apply border color to each node on the canvas matching its trust tier. "
            "Add a legend at the bottom of the canvas explaining the 4 tiers. "
            "Edit agent_builder.html and its inline JS only — no backend changes."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c1-03",
    },

    # ── PHASE C-2: AutoTune Fine-Tuning ──────────────────────────────────────

    {
        "id": "aisg-c2-01",
        "title": "DB migration — aisg_training_labels table",
        "description": (
            "Add to migration: aisg_training_labels (id SERIAL PK, "
            "example_id TEXT, input_text TEXT, output_text TEXT, "
            "label TEXT CHECK (label IN ('good','bad')), "
            "business_goal TEXT, labeled_by TEXT, "
            "created_at TIMESTAMPTZ DEFAULT NOW()). "
            "This replaces the BLEU-score-driven quality gate for non-expert users."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-c2-02",
        "title": "Build tools/aisg/autotune.py — pair generation + business-metric evaluation",
        "description": (
            "New file tools/aisg/autotune.py. "
            "Function generate_pairs_from_labels(business_goal: str) -> list[dict]. "
            "Fetches aisg_training_labels where label='good' for the given business_goal, "
            "formats as QA training pairs [{prompt, completion}], "
            "passes to existing tools/finetune/ pipeline. "
            "Function evaluate_business_metric(business_goal: str, model_version: str) -> dict "
            "with keys: metric_name, before_score, after_score, improvement_pct. "
            "Metric mapping (no ML): 'ticket_resolution' -> avg response length reduction; "
            "'false_positive_rate' -> ratio of bad-labeled outputs after tuning; "
            "'compliance_accuracy' -> pass rate on compliance test fixtures. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c2-01",
    },
    {
        "id": "aisg-c2-03",
        "title": "Build thumbs-up/down labeling UI on fine-tuning dashboard",
        "description": (
            "Edit existing fine-tuning dashboard template to add an 'AutoTune' tab. "
            "Tab content: list of recent model outputs (from fine_tuning_eval_results), "
            "each with a thumbs-up / thumbs-down button and a business_goal dropdown. "
            "Thumbs click POSTs to /api/autotune/label with example_id, label, business_goal. "
            "Replaces / supplements the existing BLEU score display — keep BLEU for Builder role, "
            "show thumbs UI for Consumer/PM roles. "
            "Edit fine-tuning template only — no backend changes in this task."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c2-02",
    },
    {
        "id": "aisg-c2-04",
        "title": "Add 'Model improved X by Y%' result card to fine-tuning dashboard",
        "description": (
            "Edit fine-tuning dashboard template: after a training run completes, "
            "call /api/autotune/evaluate?business_goal=<goal>&model_version=<ver> "
            "and render the result as a highlighted card: "
            "'Model improved [metric_name] by [improvement_pct]% this week'. "
            "Show as green card if improvement > 0, amber if no change, red if regression. "
            "Edit template + add /api/autotune/evaluate GET endpoint to tools/aisg/blueprint.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c2-03",
    },

    # ── PHASE C-3: Genesis Graduated Trust Mode ───────────────────────────────

    {
        "id": "aisg-c3-01",
        "title": "Add trust_mode config to Genesis daemon — constants + config loader",
        "description": (
            "Edit tools/genesis/constants.py: add TRUST_MODES = ('observer','advisor','executor','full'). "
            "Edit args/genesis_config.yaml: add trust_mode field defaulting to 'full' "
            "(preserves existing behavior). "
            "Edit tools/genesis/daemon.py config loader to read trust_mode and store on self. "
            "No behavior change yet — config wiring only."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-c3-02",
        "title": "Build Genesis Observer mode — log-only, zero execution",
        "description": (
            "Edit tools/genesis/daemon.py execute_reflex() method: "
            "if self.trust_mode == 'observer', log what the reflex WOULD have done "
            "to a new aisg_genesis_observer_log table (reflex_name, would_have_action, "
            "would_have_target, simulated_at) but return early without executing. "
            "Add DB migration for aisg_genesis_observer_log. "
            "Daily digest: add an observer summary section to the Genesis status dashboard "
            "showing the last 24h of simulated actions."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c3-01",
    },
    {
        "id": "aisg-c3-03",
        "title": "Build Genesis Advisor mode — reflex outputs as Kanban cards for human approval",
        "description": (
            "Edit tools/genesis/daemon.py execute_reflex(): "
            "if self.trust_mode == 'advisor', instead of executing the reflex, "
            "insert a kanban_tasks row with hitl_stage='review', "
            "title='[Genesis Suggestion] <reflex_name>: <action>', "
            "description=full reflex context, dispatch_source='genesis-advisor'. "
            "Human approves on Kanban board; scheduler picks up and executes normally. "
            "Add a filter on the Kanban board UI to show genesis-advisor cards."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c3-02",
    },
    {
        "id": "aisg-c3-04",
        "title": "Build monthly trust_mode auto-progression scheduler task",
        "description": (
            "New file tools/aisg/trust_progression.py. "
            "Function check_and_advance_trust_mode() -> dict. "
            "Reads current trust_mode from args/genesis_config.yaml. "
            "If observer: after 30 days with 0 adverse observer log events, advance to advisor. "
            "If advisor: after 30 days with >= 80% of advisor cards approved, advance to executor. "
            "If executor: after 30 days with 0 critical self-heal failures, advance to full. "
            "Writes new trust_mode back to args/genesis_config.yaml. "
            "Register as a monthly cron via tools/scheduler/ (ICDEV internal scheduler)."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aisg-c3-03",
    },

    # ── PHASE D-1: AI ROI Dashboard ───────────────────────────────────────────

    {
        "id": "aisg-d1-01",
        "title": "DB migration — aisg_roi_events table",
        "description": (
            "Add to migration: aisg_roi_events (id SERIAL PK, "
            "action_type TEXT NOT NULL, time_saved_minutes NUMERIC NOT NULL, "
            "description TEXT, triggered_by TEXT, "
            "occurred_at TIMESTAMPTZ DEFAULT NOW()). "
            "action_type CHECK constraint: "
            "('self_heal','compliance_check','security_scan','test_run','evidence_collect',"
            "'pattern_deploy','fine_tune_eval','genesis_reflex'). "
            "Seed an ROI rate table in constants: time saved per action_type in minutes."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-d1-02",
        "title": "Build tools/aisg/roi_tracker.py — ROI event emitter",
        "description": (
            "New file tools/aisg/roi_tracker.py. "
            "Function emit_roi_event(action_type: str, description: str, triggered_by: str='system'). "
            "Looks up time_saved_minutes from ROI_RATES constant dict, "
            "inserts into aisg_roi_events. "
            "Hook emit_roi_event() into: "
            "(1) tools/knowledge/self_healing.py after successful auto-remediation, "
            "(2) tools/compliance/ after evidence collection run, "
            "(3) tools/security/ after security scan completion, "
            "(4) tools/genesis/daemon.py after reflex execution. "
            "Uses get_connection(). Caller imports are additive — no existing logic changed."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-d1-01",
    },
    {
        "id": "aisg-d1-03",
        "title": "Build AI ROI dashboard page /ai-roi",
        "description": (
            "New template tools/dashboard/templates/aisg/roi.html. "
            "Hero card: total hours saved this week (sum time_saved_minutes / 60). "
            "Bar chart: hours saved by action_type (last 30 days). "
            "Weekly trend line chart (last 12 weeks). "
            "Table: recent ROI events (action_type, time_saved, description, occurred_at). "
            "Add @bp.route('/ai-roi') GET to tools/aisg/blueprint.py. "
            "Add /ai-roi to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-d1-02",
    },

    # ── PHASE D-2: Skills Gap Tracker ─────────────────────────────────────────

    {
        "id": "aisg-d2-01",
        "title": "DB migration — aisg_skill_usage table",
        "description": (
            "Add to migration: aisg_skill_usage (id SERIAL PK, "
            "user_email TEXT, skill_name TEXT, "
            "first_used_at TIMESTAMPTZ, last_used_at TIMESTAMPTZ, "
            "use_count INT DEFAULT 1, "
            "UNIQUE(user_email, skill_name)). "
            "skill_name values are the ICDEV skill IDs "
            "(e.g. 'icdev-build', 'icdev-comply', 'icdev-secure')."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-d2-02",
        "title": "Build tools/aisg/skill_gap_tracker.py — usage recorder + maturity scorer",
        "description": (
            "New file tools/aisg/skill_gap_tracker.py. "
            "Function record_skill_use(user_email, skill_name) — upserts aisg_skill_usage. "
            "Hook record_skill_use() into tools/skills/invoke.py after successful execution. "
            "Function compute_maturity_score(user_email) -> dict with "
            "score (0-100), level ('consumer'/'configurator'/'builder'), "
            "used_skills (list), unused_high_value_skills (list). "
            "Maturity bands: <10 skills = consumer, 10-20 = configurator, >20 = builder. "
            "High-value skills list defined in constants.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-d2-01",
    },
    {
        "id": "aisg-d2-03",
        "title": "Build skills gap dashboard page /ai-skills",
        "description": (
            "New template tools/dashboard/templates/aisg/skills.html. "
            "Top section: current maturity level badge + score bar. "
            "Used skills grid: icon + name + use_count for each used skill. "
            "Recommended next skills: 3 cards from unused_high_value_skills with "
            "'Why try this?' one-liner and a quick-launch link. "
            "Team view (if admin): heatmap of skill usage across all users. "
            "Add @bp.route('/ai-skills') GET to tools/aisg/blueprint.py. "
            "Add /ai-skills to .claude/commands/start.md Pages: line."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-d2-02",
    },

    # ── PHASE D-3: Talent Retention Package ──────────────────────────────────

    {
        "id": "aisg-d3-01",
        "title": "Build tools/aisg/ai_profile_encoder.py — portable AI profile export",
        "description": (
            "New file tools/aisg/ai_profile_encoder.py. "
            "Function encode_profile(user_email: str) -> dict. "
            "Aggregates: decisions from audit trail (last 180 days), "
            "custom patterns from aisg_patterns (created_by=user_email), "
            "arg overrides snapshot from git log --author, "
            "maturity score from skill_gap_tracker, "
            "learning track progress from aisg_track_progress. "
            "Returns a JSON-serializable dict; caller saves to profile_<email>.json. "
            "Extends knowledge_handoff.py export logic — call export_package() internally "
            "and add the extra fields."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-b3-01",
    },
    {
        "id": "aisg-d3-02",
        "title": "Build tools/aisg/profile_importer.py — new-hire AI profile onboarding",
        "description": (
            "New file tools/aisg/profile_importer.py. "
            "Function import_profile(profile_path: Path, new_user_email: str) -> dict. "
            "Reads profile_<email>.json from ai_profile_encoder. "
            "Imports: custom patterns (aisg_patterns INSERT), "
            "skill usage warm-start (aisg_skill_usage INSERT with use_count=0, "
            "first_used_at=NULL to show the predecessor used them), "
            "learning track activation at predecessor's achieved level, "
            "args/ overrides merged non-destructively. "
            "Logs import event to audit trail. Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aisg-d3-01",
    },

    # ── FINAL V&V ─────────────────────────────────────────────────────────────

    {
        "id": "aisg-vv-01",
        "title": "Companion sync + coherence gate across all aisg deliverables",
        "description": (
            "Gate task only — no code changes. "
            "Run: python tools/dx/companion.py --sync --write --json; "
            "python tools/workflow/coherence_checker.py --all --fix --gate. "
            "Verify: (1) all new tools/aisg/ modules registered in companion sync; "
            "(2) coherence gate passes with 0 blocking errors; "
            "(3) all new dashboard pages appear in start.md Pages: line; "
            "(4) all new DB tables have graceful existence guards. "
            "Fix all failures before marking done."
        ),
        "task_type": "chore",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aisg-d3-02",
    },
    {
        "id": "aisg-vv-02",
        "title": "Playwright E2E — all 10 new aisg pages + API routes",
        "description": (
            "Playwright E2E sign-off test. Test each new page renders 200 and key "
            "elements are present: "
            "/ai-wizard (form renders, submit returns recommendations), "
            "/dashboard/compliance-view (ATO sections visible), "
            "/dashboard/executive-view (hero ROI card visible), "
            "/dashboard/pm-view (sprint tasks visible), "
            "/ai-patterns (pattern cards render, Deploy button present), "
            "/ai-learning (3 track cards, Start Track button), "
            "/ai-handoff (Export and Import forms present), "
            "/ai-builder (canvas renders, palette populated), "
            "/ai-roi (hero hours-saved card visible), "
            "/ai-skills (maturity badge + recommendations visible). "
            "Screenshots to playwright/screenshots/aisg-<page>.png for each. "
            "Fix any 500s or missing elements before marking done."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aisg-vv-01",
    },
]

# Extra multi-parent deps (tasks that need multiple predecessors done)
EXTRA_DEPS: list[tuple[str, str]] = [
    # executive view needs ROI data (d1-02) and skill data (d2-02) for full rendering
    ("aisg-a2-03", "aisg-d1-02"),
    ("aisg-a2-03", "aisg-d2-02"),
    # pm view needs learning track data (b2-03)
    ("aisg-a2-04", "aisg-b2-03"),
    # agent builder canvas needs pattern library for tool palette population
    ("aisg-c1-03", "aisg-b1-07"),
    # vv-02 needs ALL phases done — add extra gates beyond the linear chain
    ("aisg-vv-02", "aisg-a2-05"),
    ("aisg-vv-02", "aisg-a3-03"),
    ("aisg-vv-02", "aisg-b1-07"),
    ("aisg-vv-02", "aisg-b2-04"),
    ("aisg-vv-02", "aisg-b3-03"),
    ("aisg-vv-02", "aisg-c1-04"),
    ("aisg-vv-02", "aisg-c2-04"),
    ("aisg-vv-02", "aisg-c3-04"),
    ("aisg-vv-02", "aisg-d1-03"),
    ("aisg-vv-02", "aisg-d2-03"),
    ("aisg-vv-02", "aisg-d3-02"),
]


def seed() -> None:
    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    for t in TASKS:
        cur.execute("SELECT id FROM kanban_tasks WHERE id = %s", (t["id"],))
        if cur.fetchone():
            print(f"  SKIP (exists): {t['id']}")
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO kanban_tasks
              (id, title, description, task_type, priority, status,
               scheduled_at, depends_on_task_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                t["id"],
                t["title"],
                t["description"],
                t["task_type"],
                t["priority"],
                t["status"],
                NOW,
                t["depends_on_task_id"],
                NOW,
                NOW,
            ),
        )
        print(f"  INSERT: {t['id']}")
        inserted += 1

    dep_inserted = 0
    dep_skipped = 0
    for task_id, dep_id in EXTRA_DEPS:
        cur.execute(
            "SELECT task_id FROM kanban_task_deps WHERE task_id=%s AND depends_on_id=%s",
            (task_id, dep_id),
        )
        if cur.fetchone():
            dep_skipped += 1
            continue
        cur.execute(
            "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) VALUES (%s,%s,%s)",
            (task_id, dep_id, NOW),
        )
        dep_inserted += 1

    conn.commit()
    print(
        f"\nDone — tasks: {inserted} inserted / {skipped} skipped | "
        f"extra deps: {dep_inserted} inserted / {dep_skipped} skipped"
    )


if __name__ == "__main__":
    seed()
