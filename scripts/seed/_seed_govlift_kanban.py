#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed Kanban tasks for the GovLift full-fledged federal migration automation platform.

65 atomic tasks across 6 phases (P0–P5). Idempotent — skips tasks whose IDs already exist.
gl-p0-01 is seeded as 'done' because projects.yaml was already updated.

Usage:
    python _seed_govlift_kanban.py
    python _seed_govlift_kanban.py --dry-run
"""
import argparse
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from tools.db.storage import get_connection

_NOW = datetime.now(timezone.utc).isoformat()

TASKS = [
    # ──────────────────────────────────────────────
    # PHASE 0 — Foundation (parallel, no deps)
    # ──────────────────────────────────────────────
    dict(
        id="gl-p0-01",
        title="Register GovLift multi-epic project in args/projects.yaml",
        task_type="chore",
        priority="high",
        status="done",
        depends_on_task_id=None,
        description=(
            "ALREADY COMPLETE — projects.yaml was updated in a prior session. "
            "Task is marked done so downstream tasks can unblock."
        ),
    ),
    dict(
        id="gl-p0-02",
        title="Run Creative Engine: seed migration_automation + federal_it_modernization domains",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id=None,
        description=(
            "Edit args/creative_config.yaml and add two new domains: "
            "migration_automation (keywords: runbook orchestration, cutover automation, wave-based migration, rollback playbook) "
            "and federal_it_modernization (keywords: FedRAMP migration, DISA STIG automation, ATO boundary, DoD IL4 cloud). "
            "Then run: python tools/creative/creative_engine.py --discover "
            "to seed competitor signals for Cutover.com, Rimo3, Shoreline, Moveworks. "
            "Verify at least 4 pain_point rows are inserted into creative_pain_points table."
        ),
    ),
    dict(
        id="gl-p0-03",
        title="Run Research Engine: add federal_migration vertical and generate dossier",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id=None,
        description=(
            "Edit args/research_config.yaml and add vertical: "
            "  federal_migration: "
            "    keywords: [FedRAMP migration, DISA STIG automation, ATO boundary, DoD cloud migration, IL4 workload] "
            "    sources: [nist.gov, fedramp.gov, disa.mil, gao.gov] "
            "Then run: python icdev/tools/research/research_engine.py --run --vertical federal_migration "
            "to generate a research dossier. Verify research_challenges table has rows with vertical='federal_migration'."
        ),
    ),
    dict(
        id="gl-p0-04",
        title="Add Cutover.com, Moveworks, Shoreline, Rimo3 to innovation competitive targets",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id=None,
        description=(
            "Edit args/innovation_config.yaml and add to competitive_intel.targets: "
            "  - {name: cutover.com, url: https://cutover.com, category: migration_automation} "
            "  - {name: moveworks.com, url: https://moveworks.com, category: agentic_ai} "
            "  - {name: shoreline.io, url: https://shoreline.io, category: autonomous_recovery} "
            "  - {name: rimo3.com, url: https://www.rimo3.com, category: migration_acceleration} "
            "Then run: python icdev/tools/innovation/innovation_manager.py --discover "
            "to fetch and score competitor signals. "
            "Verify innovation_signals table has at least 4 new rows."
        ),
    ),

    # ──────────────────────────────────────────────
    # PHASE 1 — Runbook Orchestration Engine
    # ──────────────────────────────────────────────
    dict(
        id="gl-p1-01",
        title="Add 5 runbook tables to tools/govlift/db/init_db.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p0-01",
        description=(
            "Open tools/govlift/db/init_db.py and add 5 new tables inside the init_govlift_db() function, "
            "after the existing table definitions. Use get_connection() and translate_sql() pattern already in the file. "
            "Tables: "
            "(1) govlift_runbooks: id TEXT PK, name TEXT NOT NULL, wave_id TEXT, status TEXT CHECK(status IN ('draft','active','archived')), "
            "steps_count INT DEFAULT 0, created_by TEXT, created_at TEXT, updated_at TEXT, classification TEXT DEFAULT 'CUI'; "
            "(2) govlift_runbook_steps: id TEXT PK, runbook_id TEXT NOT NULL REFERENCES govlift_runbooks(id), "
            "sequence INT NOT NULL, title TEXT NOT NULL, step_type TEXT CHECK(step_type IN ('manual','automated','verification','rollback','notification')), "
            "owner TEXT, estimated_minutes INT DEFAULT 30, depends_on_ids TEXT DEFAULT '[]', "
            "nist_controls_json TEXT DEFAULT '[]', skip_allowed INT DEFAULT 0, is_critical_path INT DEFAULT 0, "
            "created_at TEXT, classification TEXT DEFAULT 'CUI'; "
            "(3) govlift_runbook_executions: id TEXT PK, runbook_id TEXT NOT NULL, triggered_by TEXT, "
            "status TEXT CHECK(status IN ('pending','running','completed','rolled_back','failed')), "
            "started_at TEXT, completed_at TEXT, current_step_id TEXT, classification TEXT DEFAULT 'CUI'; "
            "(4) govlift_step_executions: id TEXT PK, execution_id TEXT NOT NULL, step_id TEXT NOT NULL, "
            "status TEXT CHECK(status IN ('pending','running','completed','failed','skipped','rolled_back')), "
            "output TEXT, started_at TEXT, completed_at TEXT, retry_count INT DEFAULT 0, classification TEXT DEFAULT 'CUI'; "
            "(5) govlift_playbook_templates: id TEXT PK, name TEXT NOT NULL, category TEXT, "
            "description TEXT, step_template_json TEXT DEFAULT '[]', nist_controls_json TEXT DEFAULT '[]', "
            "fedramp_level TEXT, step_count INT DEFAULT 0, is_certified INT DEFAULT 0, "
            "created_at TEXT, classification TEXT DEFAULT 'CUI'. "
            "All tables: CREATE TABLE IF NOT EXISTS. After adding, run python tools/govlift/db/init_db.py to verify no errors."
        ),
    ),
    dict(
        id="gl-p1-02",
        title="Add runbook/execution constants to tools/govlift/constants.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-01",
        description=(
            "Open tools/govlift/constants.py. Add the following Python constants: "
            "RUNBOOK_STATUSES = ['draft', 'active', 'archived'] "
            "STEP_TYPES = ['manual', 'automated', 'verification', 'rollback', 'notification'] "
            "EXECUTION_STATUSES = ['pending', 'running', 'completed', 'rolled_back', 'failed'] "
            "STEP_EXECUTION_STATUSES = ['pending', 'running', 'completed', 'failed', 'skipped', 'rolled_back'] "
            "TEMPLATE_CATEGORIES = ['dod_il4', 'mainframe', 'disaster_recovery', 'sap_cloud', 'network_cutover', 'general'] "
            "FEDRAMP_LEVELS = ['low', 'moderate', 'high'] "
            "These must match the CHECK constraints in the DB migration from gl-p1-01 exactly. "
            "Verify the file is importable: python -c 'from tools.govlift.constants import RUNBOOK_STATUSES; print(RUNBOOK_STATUSES)'"
        ),
    ),
    dict(
        id="gl-p1-03",
        title="Write tools/govlift/runbook_engine.py — Part A: create_runbook with DAG validation",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-02",
        description=(
            "Create tools/govlift/runbook_engine.py with Part A functions: "
            "create_runbook(name, wave_id=None, created_by=None, steps=None) → dict: "
            "  - Generates UUID for runbook id "
            "  - Validates steps list: each step is dict with keys title, step_type, sequence, depends_on_ids (list of step indices) "
            "  - Runs DFS cycle detection on step dependency DAG; raises ValueError('Cycle detected in step dependencies') if cycle found "
            "  - Validates no duplicate sequence numbers "
            "  - INSERTs into govlift_runbooks with status='draft' "
            "  - INSERTs each step into govlift_runbook_steps "
            "  - Updates govlift_runbooks.steps_count "
            "  - Returns {'runbook_id': id, 'steps_inserted': N} "
            "get_runbook(runbook_id) → dict or None: SELECT runbook + steps, return as dict with 'steps' list "
            "list_runbooks(wave_id=None, status=None) → list of dicts "
            "Use from tools.db.storage import get_connection. Use uuid.uuid4().hex for IDs. "
            "Use json.dumps/loads for depends_on_ids and nist_controls_json. "
            "Add module-level docstring: '# CUI // SP-CTI'. "
            "Verify importable: python -c 'from tools.govlift.runbook_engine import create_runbook'"
        ),
    ),
    dict(
        id="gl-p1-04",
        title="Write tools/govlift/runbook_engine.py — Part B: execution lifecycle",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-03",
        description=(
            "Add Part B functions to tools/govlift/runbook_engine.py: "
            "start_execution(runbook_id, triggered_by=None) → dict: "
            "  - Creates govlift_runbook_executions row with status='running', started_at=now "
            "  - Creates govlift_step_executions rows for all steps with status='pending' "
            "  - Fans out: sets status='running' for all steps with empty depends_on_ids (parallel start) "
            "  - Returns {'execution_id': id, 'started_steps': [step_ids]} "
            "complete_step(step_execution_id, output='', success=True) → dict: "
            "  - Updates govlift_step_executions status to 'completed' or 'failed', sets completed_at "
            "  - If success: finds eligible next steps (all depends_on_ids completed), sets them to 'running' "
            "  - Checks if all steps done → updates govlift_runbook_executions to 'completed' "
            "  - Returns {'next_steps': [ids], 'execution_complete': bool} "
            "rollback_execution(execution_id, reason='') → dict: "
            "  - Orders completed steps in reverse-dependency order (topological reverse) "
            "  - Sets each to 'rolled_back', updates execution status to 'rolled_back' "
            "  - Returns {'rolled_back_steps': N} "
            "get_execution_status(execution_id) → dict with execution + step statuses "
            "Verify: python -c 'from tools.govlift.runbook_engine import start_execution'"
        ),
    ),
    dict(
        id="gl-p1-05",
        title="Write tools/govlift/marketplace.py with 5 certified federal templates",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-02",
        description=(
            "Create tools/govlift/marketplace.py with: "
            "SEED_TEMPLATES = list of 5 template dicts, each with name, category, description, fedramp_level, is_certified=1, "
            "step_template_json (list of step dicts with title, step_type, sequence, estimated_minutes, nist_controls_json). "
            "Templates: "
            "(1) 'DoD IL4 On-Prem to AWS GovCloud Wave-Based' — category=dod_il4, fedramp_level=high, 8 steps "
            "(2) 'Legacy Mainframe Decommission' — category=mainframe, fedramp_level=moderate, 6 steps "
            "(3) 'DR Test Full Failover and Failback' — category=disaster_recovery, fedramp_level=moderate, 7 steps "
            "(4) 'SAP to Azure Government' — category=sap_cloud, fedramp_level=high, 9 steps "
            "(5) 'Network Cutover Cisco to AWS Transit Gateway' — category=network_cutover, fedramp_level=moderate, 5 steps "
            "Each template's nist_controls_json must include at least 3 NIST 800-53 controls (e.g. CM-3, CP-9, SA-3). "
            "_seed_templates(conn): INSERT IGNORE all 5 templates if not already present. "
            "list_templates(category=None) → list of dicts "
            "get_template(template_id) → dict or None "
            "instantiate_template(template_id, wave_id=None, created_by=None) → calls runbook_engine.create_runbook() with template steps "
            "Call _seed_templates at module import time. "
            "Verify: python -c 'from tools.govlift.marketplace import list_templates; print(len(list_templates()))' should print >=5"
        ),
    ),
    dict(
        id="gl-p1-06",
        title="Add runbook page routes to tools/govlift/blueprint.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-04",
        description=(
            "Open tools/govlift/blueprint.py. Add 4 new page routes: "
            "GET /govlift/runbooks → renders govlift/runbooks.html with list_runbooks() data "
            "GET /govlift/runbooks/<runbook_id> → renders govlift/runbook_detail.html with get_runbook() data "
            "GET /govlift/runbooks/<runbook_id>/execute → renders govlift/runbook_execute.html "
            "  (requires execution_id query param or creates new execution) "
            "GET /govlift/marketplace → renders govlift/marketplace.html with list_templates() data "
            "Import from tools.govlift.runbook_engine and tools.govlift.marketplace at top of file. "
            "Each route: wrap in try/except, log errors, return 500 on failure. "
            "Also add these new page routes to .claude/commands/start.md Pages: line: "
            "/govlift/runbooks, /govlift/marketplace "
            "Verify: python -c 'from tools.govlift.blueprint import bp; print([r.rule for r in bp.url_map.iter_rules()])' "
            "Wait — blueprint routes are registered on app. Instead verify: "
            "python -c 'from tools.govlift import blueprint; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p1-07",
        title="Add runbook API routes to tools/govlift/blueprint.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-04",
        description=(
            "Add 9 API routes to tools/govlift/blueprint.py: "
            "POST /api/govlift/runbooks → create_runbook(name, wave_id, steps from JSON body) → 201 "
            "GET /api/govlift/runbooks → list_runbooks(wave_id=, status= from query params) → 200 "
            "GET /api/govlift/runbooks/<id> → get_runbook(id) → 200 or 404 "
            "POST /api/govlift/runbooks/<id>/execute → start_execution(id, triggered_by from auth) → 201 "
            "POST /api/govlift/executions/<id>/step/<sid>/complete → complete_step(sid, output, success from body) → 200 "
            "GET /api/govlift/executions/<id>/status → get_execution_status(id) → 200 "
            "POST /api/govlift/executions/<id>/rollback → rollback_execution(id, reason from body) → 200 "
            "GET /api/govlift/templates → list_templates(category= from query) → 200 "
            "POST /api/govlift/templates/<id>/instantiate → instantiate_template(id, wave_id from body) → 201 "
            "All routes: return jsonify({'success': True, 'data': ...}) or {'success': False, 'error': msg}. "
            "Use from flask import request, jsonify. "
            "Verify no import errors: python -c 'from tools.govlift.blueprint import bp; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p1-08",
        title="Create tools/dashboard/templates/govlift/runbooks.html — runbook list view",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-06",
        description=(
            "Create tools/dashboard/templates/govlift/runbooks.html. "
            "Extends 'base.html'. Title: 'GovLift — Runbooks'. "
            "Include IQE widget: {% include 'includes/iqe_query_widget.html' %} with canvas='govlift'. "
            "Layout: "
            "- Top stat cards (dark theme, green #34d399 accent): Total Runbooks, Active Executions, Templates Available, Completed This Month "
            "- Filter bar: search by name (text input), filter by wave (select from waves list), filter by status (draft/active/archived) "
            "- Runbook table: name, wave, status badge (color-coded), steps count, created_by, created_at, actions (View, Execute) "
            "- 'New Runbook' button (top right): opens a modal with template selector — list_templates() data rendered as cards "
            "  with Instantiate button that POSTs to /api/govlift/templates/<id>/instantiate "
            "- Empty state: 'No runbooks yet. Start from a certified template.' with link to /govlift/marketplace "
            "Dark theme matching existing govlift templates. CUI banner at top. "
            "Template receives context vars: runbooks (list), waves (list), templates (list), stats (dict)."
        ),
    ),
    dict(
        id="gl-p1-09",
        title="Create tools/dashboard/templates/govlift/runbook_detail.html — step DAG view",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-08",
        description=(
            "Create tools/dashboard/templates/govlift/runbook_detail.html. "
            "Extends 'base.html'. Title: 'Runbook: {{ runbook.name }}'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout: "
            "- Breadcrumb: GovLift > Runbooks > {{ runbook.name }} "
            "- Header: name, status badge, wave badge, created_by, step count "
            "- Step DAG visualization: Use a simple HTML/CSS/JS approach with SVG arrows between step cards "
            "  (do NOT require Sigma.js which may not be installed — use vanilla JS). "
            "  Each step card: title, step_type badge, owner, estimated_minutes, NIST controls chips, depends_on labels "
            "  Color-code by step_type: manual=blue, automated=green, verification=yellow, rollback=red, notification=gray "
            "- Step table below DAG: sequence, title, type, owner, est. time, depends on, critical path indicator "
            "- Action buttons: 'Start Execution' (POST to /api/govlift/runbooks/<id>/execute, redirect to /govlift/runbooks/<id>/execute?execution_id=...), "
            "  'Edit' (placeholder), 'Archive' "
            "- NIST Controls Coverage section: list all unique controls across all steps "
            "Template receives: runbook (dict with nested steps list), waves (list)."
        ),
    ),
    dict(
        id="gl-p1-10",
        title="Create tools/dashboard/templates/govlift/runbook_execute.html — live execution dashboard",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-09",
        description=(
            "Create tools/dashboard/templates/govlift/runbook_execute.html. "
            "Extends 'base.html'. Title: 'Executing: {{ runbook.name }}'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout: "
            "- Top bar: runbook name, execution ID, triggered_by, elapsed time (JS counter), status badge "
            "- Progress bar: (completed_steps / total_steps) * 100% with green fill "
            "- Step status grid: each step as a card with color-coded status: "
            "  pending=gray, running=blue pulse animation, completed=green, failed=red, skipped=yellow, rolled_back=orange "
            "  Each card shows: title, step_type, owner, status. Running cards show spinner. "
            "- Auto-polling: JavaScript setInterval every 3000ms calls GET /api/govlift/executions/<execution_id>/status "
            "  and updates step cards and progress bar without page reload "
            "- Manual step completion form: step selector (dropdown of running steps), output textarea, success/fail radio, Submit button "
            "  POST to /api/govlift/executions/<id>/step/<sid>/complete "
            "- Rollback button (danger): POST to /api/govlift/executions/<id>/rollback with confirm dialog "
            "- Completion banner: shown when execution_complete=true, with download artifacts button (Phase 2 hook) "
            "Template receives: runbook (dict), execution (dict), step_executions (list)."
        ),
    ),
    dict(
        id="gl-p1-11",
        title="Create tools/dashboard/templates/govlift/marketplace.html — template library",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-06",
        description=(
            "Create tools/dashboard/templates/govlift/marketplace.html. "
            "Extends 'base.html'. Title: 'GovLift — Playbook Marketplace'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout: "
            "- Header: 'Pre-Certified Federal Migration Playbooks' with subtitle "
            "- Filter bar: category buttons (All / DoD IL4 / Mainframe / DR Test / SAP Cloud / Network Cutover) "
            "- Template card grid (3 columns): each card shows: "
            "  name, category badge, FedRAMP level badge (Low/Moderate/High), "
            "  step count, NIST controls covered (count), certified badge (green checkmark if is_certified), "
            "  description (first 120 chars), 'Instantiate' button "
            "- Instantiate button: opens modal asking for Wave selection (dropdown of waves), "
            "  then POSTs to /api/govlift/templates/<id>/instantiate, redirects to new runbook detail page "
            "- 'Submit a Template' call-to-action at bottom (placeholder) "
            "- Dark theme, CUI banner. "
            "Template receives: templates (list), waves (list)."
        ),
    ),
    dict(
        id="gl-p1-12",
        title="Mirror 4 new runbook templates to icdev/tools/dashboard/templates/govlift/",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-11",
        description=(
            "Copy the 4 new template files to the icdev/ package mirror: "
            "copy tools/dashboard/templates/govlift/runbooks.html → icdev/tools/dashboard/templates/govlift/runbooks.html "
            "copy tools/dashboard/templates/govlift/runbook_detail.html → icdev/tools/dashboard/templates/govlift/runbook_detail.html "
            "copy tools/dashboard/templates/govlift/runbook_execute.html → icdev/tools/dashboard/templates/govlift/runbook_execute.html "
            "copy tools/dashboard/templates/govlift/marketplace.html → icdev/tools/dashboard/templates/govlift/marketplace.html "
            "Use shutil.copy2() or pathlib.Path.write_text(source.read_text()). "
            "Verify all 4 files exist in icdev/tools/dashboard/templates/govlift/."
        ),
    ),
    dict(
        id="gl-p1-13",
        title="Add govlift_step_executions and govlift_runbook_executions to APPEND_ONLY_TABLES",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-01",
        description=(
            "Open .claude/hooks/pre_tool_use.py. "
            "Find the APPEND_ONLY_TABLES list or set. "
            "Add 'govlift_step_executions' and 'govlift_runbook_executions' to the list. "
            "These are append-only for NIST AU compliance — executions must never be edited or deleted. "
            "Verify the hook file is still valid Python: python .claude/hooks/pre_tool_use.py --help or python -c 'import ast; ast.parse(open(\".claude/hooks/pre_tool_use.py\").read()); print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p1-14",
        title="Update tools/iqe/adapters/govlift.py — add runbook/execution/template collections",
        task_type="build",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p1-07",
        description=(
            "Open tools/iqe/adapters/govlift.py. "
            "Add 3 new IQE collections alongside existing ones: "
            "(1) 'runbooks' collection: 5 seed queries: "
            "  'show all active runbooks', 'list runbooks for wave {wave_name}', "
            "  'which runbooks are in draft status', 'show runbook step count', 'find runbooks created this week' "
            "  SQL: SELECT * FROM govlift_runbooks WHERE ... "
            "(2) 'executions' collection: 5 seed queries: "
            "  'show running executions', 'list failed executions today', "
            "  'which executions completed this week', 'show execution step status for {execution_id}', 'list rolled back executions' "
            "  SQL: SELECT * FROM govlift_runbook_executions JOIN govlift_step_executions ... "
            "(3) 'templates' collection: 3 seed queries: "
            "  'list certified playbook templates', 'show DoD IL4 templates', 'find templates with FedRAMP High' "
            "  SQL: SELECT * FROM govlift_playbook_templates WHERE ... "
            "Each collection uses graceful table-missing handling (try/except). "
            "Also add 5 seed query files to context/iqe/queries/govlift/ for these new collections."
        ),
    ),
    dict(
        id="gl-p1-15",
        title="Update govlift index.html nav and add Runbooks/Marketplace quick-action cards",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p1-08",
        description=(
            "Open tools/dashboard/templates/govlift/index.html. "
            "Add 'Runbooks' and 'Marketplace' to the sidebar navigation links. "
            "Add 2 new quick-action stat cards: "
            "  - 'Runbooks' card: shows count from govlift_runbooks, links to /govlift/runbooks "
            "  - 'Marketplace' card: shows certified template count, links to /govlift/marketplace "
            "Also open tools/govlift/blueprint.py and update the / index route to pass runbooks_count and templates_count to template. "
            "Mirror updated index.html to icdev/tools/dashboard/templates/govlift/index.html."
        ),
    ),
    dict(
        id="gl-p1-16",
        title="Write pytest unit tests for tools/govlift/runbook_engine.py",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-04",
        description=(
            "Create tests/test_govlift_runbook_engine.py. "
            "Use pytest with SQLite in-memory DB (conftest.py ICDEV_STORAGE_BACKEND=sqlite handles this). "
            "Test functions: "
            "test_create_runbook_success(): creates runbook with 3 linear steps, asserts runbook_id returned, steps_count=3 "
            "test_create_runbook_dag_cycle_raises(): creates steps where step 2 depends on step 3 and step 3 depends on step 2, asserts ValueError raised "
            "test_create_runbook_duplicate_sequence_raises(): creates steps with duplicate sequence=1, asserts ValueError raised "
            "test_start_execution_creates_records(): start_execution on valid runbook, assert execution row created with status='running' "
            "test_start_execution_fans_out_parallel_steps(): runbook with 2 independent first steps, start_execution fans both to 'running' "
            "test_complete_step_advances_next(): 2-step linear runbook, complete step 1, assert step 2 becomes 'running' "
            "test_complete_step_marks_execution_done(): single-step runbook, complete step, assert execution status='completed' "
            "test_rollback_fires_reverse_order(): 3-step linear, complete steps 1+2, rollback, assert steps rolled back in reverse order "
            "Run: pytest tests/test_govlift_runbook_engine.py -v --tb=short"
        ),
    ),
    dict(
        id="gl-p1-17",
        title="Write pytest unit tests for tools/govlift/marketplace.py",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-05",
        description=(
            "Create tests/test_govlift_marketplace.py. "
            "Test functions: "
            "test_list_templates_returns_five_seeded(): call list_templates(), assert len >= 5 "
            "test_each_template_has_nist_controls(): for each template, parse nist_controls_json, assert len >= 3 "
            "test_each_template_is_certified(): assert all seeded templates have is_certified=1 "
            "test_instantiate_creates_runbook(): call instantiate_template(template_id), "
            "  get returned runbook_id, assert get_runbook(runbook_id) has correct step count matching template "
            "test_get_template_not_found(): call get_template('nonexistent'), assert returns None "
            "test_filter_by_category(): call list_templates(category='dod_il4'), assert all results have category='dod_il4' "
            "Run: pytest tests/test_govlift_marketplace.py -v --tb=short"
        ),
    ),
    dict(
        id="gl-p1-18",
        title="Update tools/manifest/govlift.md with runbook_engine.py and marketplace.py entries",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p1-17",
        description=(
            "Open tools/manifest/govlift.md. "
            "Add entries for the 2 new modules: "
            "runbook_engine.py: functions create_runbook, get_runbook, list_runbooks, start_execution, "
            "complete_step, rollback_execution, get_execution_status; tables govlift_runbooks, govlift_runbook_steps, "
            "govlift_runbook_executions, govlift_step_executions "
            "marketplace.py: functions list_templates, get_template, instantiate_template; table govlift_playbook_templates "
            "Update the page routes table to include the 4 new page routes. "
            "Update the API routes table to include the 9 new API routes. "
            "Update table count from 7 to 12."
        ),
    ),
    dict(
        id="gl-p1-19",
        title="Run coherence checker + companion sync + create Phase 1 feature doc",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-18",
        description=(
            "Run in order: "
            "1. python tools/workflow/coherence_checker.py --all --fix --gate "
            "   (fix any coherence errors before proceeding) "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. Create docs/features/phase-3-govlift-runbook-engine.md with sections: "
            "   ## Overview, ## New Tables (5), ## New Modules (2), ## New Pages (4), "
            "   ## API Routes (9), ## Acceptance Criteria "
            "   Acceptance criteria: create runbook from template in ≤3 clicks, "
            "   live execution dashboard updates within 3s, rollback fires in reverse-dep order "
            "4. pytest tests/test_govlift_runbook_engine.py tests/test_govlift_marketplace.py -v --tb=short "
            "   (all tests must pass before marking this task done)"
        ),
    ),

    # ──────────────────────────────────────────────
    # PHASE 2 — Compliance-as-Code Layer
    # ──────────────────────────────────────────────
    dict(
        id="gl-p2-01",
        title="Add 3 compliance tables to tools/govlift/db/init_db.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p1-19",
        description=(
            "Open tools/govlift/db/init_db.py. Add 3 new tables: "
            "(1) govlift_compliance_artifacts: id TEXT PK, execution_id TEXT NOT NULL, "
            "step_id TEXT, control_id TEXT NOT NULL, control_family TEXT, "
            "artifact_type TEXT CHECK(artifact_type IN ('ssp_delta','poam_entry','stig_finding','evidence','sbom_diff')), "
            "content_json TEXT DEFAULT '{}', generated_at TEXT, classification TEXT DEFAULT 'CUI'; "
            "(2) govlift_ato_boundaries: id TEXT PK, name TEXT NOT NULL, workload_ids_json TEXT DEFAULT '[]', "
            "boundary_type TEXT, fedramp_level TEXT, classification_level TEXT, "
            "vendor_list_json TEXT DEFAULT '[]', last_assessed TEXT, classification TEXT DEFAULT 'CUI'; "
            "(3) govlift_supply_chain_risks: id TEXT PK, runbook_id TEXT, vendor_name TEXT NOT NULL, "
            "risk_level TEXT CHECK(risk_level IN ('low','medium','high','critical')), "
            "cve_count INT DEFAULT 0, findings_json TEXT DEFAULT '[]', "
            "scanned_at TEXT, classification TEXT DEFAULT 'CUI'. "
            "All: CREATE TABLE IF NOT EXISTS. "
            "Verify: python tools/govlift/db/init_db.py"
        ),
    ),
    dict(
        id="gl-p2-02",
        title="Write tools/govlift/compliance_tracker.py — Part A: emit_evidence + generate_artifacts_package",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-01",
        description=(
            "Create tools/govlift/compliance_tracker.py with Part A: "
            "emit_evidence(step_execution_id, control_id, description, artifacts_dict=None, artifact_type='evidence') → str: "
            "  - Validates control_id matches NIST 800-53 pattern (e.g. 'AC-2', 'CM-3') "
            "  - Extracts control_family from first two chars of control_id "
            "  - INSERTs into govlift_compliance_artifacts with content_json=json.dumps(artifacts_dict or {}) "
            "  - Returns inserted artifact id "
            "generate_artifacts_package(execution_id) → dict: "
            "  - SELECTs all govlift_compliance_artifacts WHERE execution_id=... "
            "  - Groups by control_family, counts by artifact_type "
            "  - Returns dict: {'ssp_delta': [...], 'poam_entries': [...], 'stig_summary': {...}, "
            "    'control_families_covered': {'AC': N, 'CM': N, ...}, 'total_artifacts': N} "
            "Use: from tools.db.storage import get_connection "
            "Module docstring: # CUI // SP-CTI "
            "Verify: python -c 'from tools.govlift.compliance_tracker import emit_evidence; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p2-03",
        title="Write tools/govlift/compliance_tracker.py — Part B: ATO boundary + supply chain",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-02",
        description=(
            "Add Part B functions to tools/govlift/compliance_tracker.py: "
            "assess_ato_boundary_impact(workload_ids) → dict: "
            "  - SELECTs govlift_ato_boundaries where workload_ids_json overlaps workload_ids "
            "  - Flags classification changes: if any workload classification > boundary classification_level "
            "  - Flags new vendors: compares vendor_list_json against known vendors "
            "  - Returns {'boundaries_affected': N, 'classification_flags': [...], 'vendor_flags': [...]} "
            "run_supply_chain_scan(runbook_id) → dict: "
            "  - SELECTs govlift_workloads WHERE wave_id in runbook's wave_id, extracts vendor info "
            "  - Cross-references govlift_supply_chain_risks for known risks "
            "  - Computes aggregate risk_level (max of found risks) "
            "  - INSERTs scan results into govlift_supply_chain_risks "
            "  - Returns {'risk_level': str, 'cve_count': N, 'vendors_scanned': N, 'findings': [...]} "
            "Verify: python -c 'from tools.govlift.compliance_tracker import assess_ato_boundary_impact; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p2-04",
        title="Wire compliance_tracker.emit_evidence into runbook_engine.complete_step",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-03",
        description=(
            "Open tools/govlift/runbook_engine.py. "
            "In the complete_step() function: "
            "After updating step status, import and call compliance_tracker: "
            "  from tools.govlift.compliance_tracker import emit_evidence "
            "  step = get the runbook step record (SELECT from govlift_runbook_steps) "
            "  nist_controls = json.loads(step['nist_controls_json'] or '[]') "
            "  if success and nist_controls: "
            "    for control_id in nist_controls: "
            "      emit_evidence(step_execution_id, control_id, f'Step {step[\"title\"]} completed', "
            "        artifact_type='evidence') "
            "  elif not success and nist_controls: "
            "    for control_id in nist_controls: "
            "      emit_evidence(step_execution_id, control_id, f'Step {step[\"title\"]} FAILED — POAM candidate', "
            "        artifact_type='poam_entry') "
            "Wrap in try/except so compliance failures don't break step execution. "
            "Verify: existing runbook_engine tests still pass after change."
        ),
    ),
    dict(
        id="gl-p2-05",
        title="Add compliance API routes to tools/govlift/blueprint.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-04",
        description=(
            "Add 4 compliance API routes to tools/govlift/blueprint.py: "
            "GET /api/govlift/executions/<id>/artifacts → generate_artifacts_package(id) → 200 "
            "POST /api/govlift/executions/<id>/artifacts/export → "
            "  generates ZIP with 4 files: ssp_delta.json, poam_draft.json, stig_checklist.json, sbom_diff.json "
            "  Returns ZIP as binary response with Content-Type: application/zip "
            "  Use Python zipfile module in memory (io.BytesIO) "
            "GET /api/govlift/runbooks/<id>/ato-impact → assess_ato_boundary_impact(workload_ids from runbook's wave workloads) → 200 "
            "GET /api/govlift/supply-chain/<runbook_id> → run_supply_chain_scan(runbook_id) → 200 "
            "Also add GET /govlift/compliance page route → renders govlift/compliance.html "
            "Add /govlift/compliance to .claude/commands/start.md Pages: line."
        ),
    ),
    dict(
        id="gl-p2-06",
        title="Create tools/dashboard/templates/govlift/compliance.html — compliance artifact viewer",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-05",
        description=(
            "Create tools/dashboard/templates/govlift/compliance.html. "
            "Extends 'base.html'. Title: 'GovLift — Compliance Artifacts'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout: "
            "- NIST 800-53 Control Family Heatmap: 7 family tiles (AC, AU, CM, CP, IA, SC, SI) "
            "  Each tile: family code, full name, coverage count, color gradient from gray (0 artifacts) to green (10+) "
            "  Click tile → filters artifact table below "
            "- SSP Delta Preview: table of ssp_delta artifacts with control_id, description, step source "
            "- POAM Auto-Draft: table of poam_entry artifacts with control_id, failed step, recommended_action, status "
            "- Supply Chain Risk Alerts: panel showing run_supply_chain_scan results with risk level badge "
            "- Download Artifacts Package: button that POSTs to /api/govlift/executions/<id>/artifacts/export "
            "  (requires execution_id selection via dropdown) "
            "- Execution selector at top: dropdown of recent executions to view artifacts for "
            "  When selected, JS fetches GET /api/govlift/executions/<id>/artifacts and re-renders panels "
            "Dark theme, CUI banner. Template receives: executions (list), artifacts (dict from generate_artifacts_package)."
        ),
    ),
    dict(
        id="gl-p2-07",
        title="Mirror compliance.html to icdev/ and add to nav sidebar",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-06",
        description=(
            "1. Copy tools/dashboard/templates/govlift/compliance.html → icdev/tools/dashboard/templates/govlift/compliance.html "
            "2. Open tools/dashboard/templates/govlift/index.html and add 'Compliance' link to sidebar nav "
            "3. Open tools/govlift/blueprint.py, verify GET /govlift/compliance route exists (added in gl-p2-05) "
            "4. Mirror updated index.html to icdev/tools/dashboard/templates/govlift/index.html "
            "Verify: python -c 'from tools.govlift.blueprint import bp; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p2-08",
        title="Add govlift_compliance_artifacts and govlift_supply_chain_risks to APPEND_ONLY_TABLES",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-01",
        description=(
            "Open .claude/hooks/pre_tool_use.py. "
            "Add 'govlift_compliance_artifacts' and 'govlift_supply_chain_risks' to APPEND_ONLY_TABLES. "
            "These are append-only for NIST AU compliance — evidence must never be edited or deleted. "
            "Verify the file is valid Python."
        ),
    ),
    dict(
        id="gl-p2-09",
        title="Add compliance_tracking section to args/govlift_config.yaml",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p2-03",
        description=(
            "Open args/govlift_config.yaml (create if missing). "
            "Add or merge the compliance_tracking section: "
            "compliance_tracking: "
            "  emit_evidence: true "
            "  auto_generate_artifacts: true "
            "  nist_control_families: [AC, AU, CM, CP, IA, SC, SI] "
            "  artifact_retention_years: 7 "
            "  poam_auto_draft: true "
            "  supply_chain_scan_on_execute: true "
            "Verify valid YAML: python -c 'import yaml; yaml.safe_load(open(\"args/govlift_config.yaml\"))'"
        ),
    ),
    dict(
        id="gl-p2-10",
        title="Write pytest unit tests for tools/govlift/compliance_tracker.py",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-04",
        description=(
            "Create tests/test_govlift_compliance_tracker.py. "
            "Test functions: "
            "test_emit_evidence_inserts_row(): call emit_evidence('exec1', 'AC-2', 'test'), "
            "  assert row exists in govlift_compliance_artifacts with control_family='AC' "
            "test_emit_evidence_invalid_control_raises(): call with control_id='INVALID', assert ValueError raised "
            "test_generate_artifacts_package_groups_by_family(): emit 3 artifacts for AC, 2 for CM, "
            "  call generate_artifacts_package, assert control_families_covered has AC=3, CM=2 "
            "test_assess_ato_boundary_impact_flags_classification_change(): seed boundary with classification_level='CUI', "
            "  add workload with classification='SECRET', assert classification_flags is non-empty "
            "test_run_supply_chain_scan_returns_risk_level(): call run_supply_chain_scan('runbook_id'), "
            "  assert result has 'risk_level' key with value in ['low','medium','high','critical','none'] "
            "Run: pytest tests/test_govlift_compliance_tracker.py -v --tb=short"
        ),
    ),
    dict(
        id="gl-p2-11",
        title="Update IQE + manifest + coherence + create Phase 2 feature doc",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-10",
        description=(
            "1. Open tools/iqe/adapters/govlift.py, add 2 collections: "
            "   'compliance_artifacts' (5 seed queries: 'show compliance artifacts for execution X', "
            "   'list POAM entries from last migration', 'which NIST controls were satisfied', "
            "   'show supply chain risk findings', 'count evidence artifacts by control family') "
            "   'supply_chain_risks' (5 queries similar pattern) "
            "2. Update tools/manifest/govlift.md: add compliance_tracker.py entry, update table count to 15 "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "4. python tools/dx/companion.py --sync --write --json "
            "5. Create docs/features/phase-3-govlift-compliance-as-code.md with sections: "
            "   Overview, New Tables (3), New Module, New Page, API Routes (4), Acceptance Criteria "
            "   Acceptance: every completed execution produces downloadable artifacts ZIP; "
            "   SSP delta identifies ≥5 NIST controls satisfied "
            "6. pytest tests/test_govlift_compliance_tracker.py -v --tb=short (all must pass)"
        ),
    ),

    # ──────────────────────────────────────────────
    # PHASE 3 — Agentic Autonomous Recovery
    # ──────────────────────────────────────────────
    dict(
        id="gl-p3-01",
        title="Add govlift_recovery_decisions and govlift_dependency_graph tables",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p2-11",
        description=(
            "Open tools/govlift/db/init_db.py. Add 2 new tables: "
            "(1) govlift_recovery_decisions: id TEXT PK, execution_id TEXT NOT NULL, step_id TEXT, "
            "failure_reason TEXT, decision TEXT CHECK(decision IN ('retry','skip','rollback','escalate')), "
            "confidence_score REAL DEFAULT 0.5, reasoning TEXT, agent_id TEXT, decided_at TEXT, "
            "outcome TEXT, classification TEXT DEFAULT 'CUI'; "
            "(2) govlift_dependency_graph: id TEXT PK, source_workload_id TEXT NOT NULL, "
            "target_workload_id TEXT NOT NULL, dependency_type TEXT CHECK(dependency_type IN ('network','data','auth','service')), "
            "criticality TEXT CHECK(criticality IN ('low','medium','high','critical')), "
            "classification TEXT DEFAULT 'CUI'. "
            "All: CREATE TABLE IF NOT EXISTS. "
            "Verify: python tools/govlift/db/init_db.py"
        ),
    ),
    dict(
        id="gl-p3-02",
        title="Write tools/govlift/dependency_mapper.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-01",
        description=(
            "Create tools/govlift/dependency_mapper.py with: "
            "ingest_dependency_graph(workload_ids, source='manual') → int (rows inserted): "
            "  - For each pair in workload_ids, if no existing edge, prompt user to specify relationship "
            "  - Actually: accepts edges as list of dicts {source_workload_id, target_workload_id, dependency_type, criticality} "
            "  - INSERTs into govlift_dependency_graph, returns count "
            "get_critical_path(runbook_id) → list of step_ids: "
            "  - Gets all runbook steps and their depends_on_ids "
            "  - Runs longest-path algorithm (Bellman-Ford on negated weights where weight=estimated_minutes) "
            "  - Returns ordered list of step_ids on critical path "
            "get_blast_radius(step_id, execution_id) → list of step_ids: "
            "  - BFS from failed step through all steps that depend on it (directly or transitively) "
            "  - Returns list of downstream step_ids in BFS order "
            "export_as_d3_json(runbook_id) → dict: "
            "  - Returns {'nodes': [{id, label, type, criticality}], 'links': [{source, target, dependency_type}]} "
            "  - Suitable for D3.js force-directed graph rendering "
            "Module docstring: # CUI // SP-CTI "
            "Verify: python -c 'from tools.govlift.dependency_mapper import get_blast_radius; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p3-03",
        title="Write tools/govlift/agentic_recovery.py — Part A: analyze_failure decision tree",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-02",
        description=(
            "Create tools/govlift/agentic_recovery.py with Part A: "
            "analyze_failure(step_execution_id) → dict: "
            "  - Reads step_execution from govlift_step_executions (failure_reason, retry_count) "
            "  - Reads runbook_step from govlift_runbook_steps (is_critical_path, skip_allowed, step_type) "
            "  - Decision tree: "
            "    if retry_count < 3 and not is_critical_path: decision='retry', confidence=0.85 "
            "    elif is_critical_path and retry_count >= 3: decision='rollback', confidence=0.90 "
            "    elif not is_critical_path and skip_allowed and retry_count >= 3: decision='skip', confidence=0.70 "
            "    else: decision='escalate', confidence=0.60 "
            "  - INSERTs into govlift_recovery_decisions with reasoning explaining which branch fired "
            "  - Returns {'decision_id': id, 'decision': str, 'confidence_score': float, 'reasoning': str} "
            "RECOVERY_DECISIONS = ['retry', 'skip', 'rollback', 'escalate'] — add to constants.py too "
            "Module docstring: # CUI // SP-CTI "
            "Verify: python -c 'from tools.govlift.agentic_recovery import analyze_failure; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p3-04",
        title="Write tools/govlift/agentic_recovery.py — Part B: cascade predict + apply + learn",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-03",
        description=(
            "Add Part B functions to tools/govlift/agentic_recovery.py: "
            "predict_cascade(step_id, execution_id) → list of dicts: "
            "  - Calls dependency_mapper.get_blast_radius(step_id, execution_id) "
            "  - For each downstream step, computes failure_probability = base_prob * (1.0 / (1 + distance)) "
            "  - Returns list sorted by probability desc: [{step_id, step_title, failure_probability, distance}] "
            "apply_decision(recovery_decision_id) → dict: "
            "  - Reads govlift_recovery_decisions row "
            "  - Calls appropriate runbook_engine function: "
            "    retry → increment retry_count, set step_execution back to 'running' "
            "    skip → complete_step(step_execution_id, output='SKIPPED', success=True) "
            "    rollback → rollback_execution(execution_id, reason='Agentic: ' + reasoning) "
            "    escalate → adds escalation note to step_execution output, sets status='failed' "
            "  - Returns {'applied': True, 'action': decision} "
            "learn_from_outcome(recovery_decision_id, outcome) → float: "
            "  - outcome: 'success' or 'failure' "
            "  - Updates govlift_recovery_decisions.confidence_score: +=0.05 if success, -=0.05 if failure "
            "  - Clamps to [0.0, 1.0] "
            "  - Updates outcome column "
            "  - Returns new confidence_score "
            "Verify: python -c 'from tools.govlift.agentic_recovery import predict_cascade, apply_decision; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p3-05",
        title="Wire agentic_recovery into runbook_engine.complete_step on failure",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-04",
        description=(
            "Open tools/govlift/runbook_engine.py. "
            "In complete_step(), after setting step status to 'failed': "
            "  from tools.govlift.agentic_recovery import analyze_failure, apply_decision "
            "  recovery = analyze_failure(step_execution_id) "
            "  apply_decision(recovery['decision_id']) "
            "Wrap in try/except — if agentic recovery itself fails, log the error but don't re-raise "
            "(step is already marked failed, execution continues to recovery path or escalation). "
            "The returned dict from complete_step should include: 'recovery_decision': recovery "
            "Verify: run existing runbook_engine tests — they should still pass since we wrap in try/except."
        ),
    ),
    dict(
        id="gl-p3-06",
        title="Add recovery API routes to tools/govlift/blueprint.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-05",
        description=(
            "Add 4 recovery API routes to tools/govlift/blueprint.py: "
            "POST /api/govlift/executions/<id>/step/<sid>/recover → "
            "  accepts {decision: retry|skip|rollback|escalate} in body "
            "  creates manual recovery_decision row with agent_id='human_override' "
            "  calls apply_decision() → 200 "
            "GET /api/govlift/executions/<id>/cascade-forecast → "
            "  finds current failed step in execution, calls predict_cascade() → 200 "
            "GET /api/govlift/workloads/dependency-graph → "
            "  calls export_as_d3_json() for all workloads → 200 (D3-renderable JSON) "
            "GET /api/govlift/recovery → "
            "  SELECTs govlift_recovery_decisions ordered by decided_at desc, limit 100 "
            "  Supports filter by decision= and outcome= query params → 200 "
            "Also add GET /govlift/recovery page route → renders govlift/recovery.html "
            "Add /govlift/recovery to .claude/commands/start.md Pages: line."
        ),
    ),
    dict(
        id="gl-p3-07",
        title="Create tools/dashboard/templates/govlift/recovery.html",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-06",
        description=(
            "Create tools/dashboard/templates/govlift/recovery.html. "
            "Extends 'base.html'. Title: 'GovLift — Agentic Recovery'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout: "
            "- Stat cards: Total Recoveries, Success Rate, Escalations, Active Monitoring "
            "- Recovery Event Log table: step title, execution ID, decision (badge: retry=blue/skip=yellow/rollback=orange/escalate=red), "
            "  confidence score (progress bar), reasoning (truncated), outcome (success/failure badge), decided_at "
            "- D3 Force-Directed Dependency Graph: "
            "  Load data from GET /api/govlift/workloads/dependency-graph "
            "  Render with D3.js (load from CDN: https://d3js.org/d3.v7.min.js) "
            "  Nodes colored by criticality (critical=red, high=orange, medium=yellow, low=green) "
            "  On hover: show tooltip with workload name and dependencies "
            "- Cascade Forecast Panel: "
            "  Select an execution (dropdown), click 'Forecast' → "
            "  fetches GET /api/govlift/executions/<id>/cascade-forecast "
            "  shows table of at-risk steps with failure_probability as color-coded badge "
            "- Manual Override Form: for escalated decisions, select step and decision, POST to /recover "
            "Dark theme, CUI banner."
        ),
    ),
    dict(
        id="gl-p3-08",
        title="Mirror recovery.html to icdev/ and add to nav sidebar",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-07",
        description=(
            "1. Copy tools/dashboard/templates/govlift/recovery.html → icdev/tools/dashboard/templates/govlift/recovery.html "
            "2. Open tools/dashboard/templates/govlift/index.html and add 'Recovery' link to sidebar nav "
            "3. Mirror updated index.html to icdev/tools/dashboard/templates/govlift/index.html "
            "Verify file exists: python -c 'import pathlib; assert pathlib.Path(\"icdev/tools/dashboard/templates/govlift/recovery.html\").exists(); print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p3-09",
        title="Add govlift_recovery_decisions to APPEND_ONLY_TABLES",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-01",
        description=(
            "Open .claude/hooks/pre_tool_use.py. "
            "Add 'govlift_recovery_decisions' to APPEND_ONLY_TABLES. "
            "Recovery audit records must never be modified after creation (NIST AU). "
            "Verify valid Python."
        ),
    ),
    dict(
        id="gl-p3-10",
        title="Write pytest unit tests for dependency_mapper and agentic_recovery",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-05",
        description=(
            "Create tests/test_govlift_agentic_recovery.py. "
            "test_blast_radius_no_cycle(): seed 3 steps A→B→C, fail A, assert get_blast_radius returns [B, C] in BFS order "
            "test_blast_radius_parallel_branches(): A→B, A→C, fail A, assert blast radius includes both B and C "
            "test_analyze_failure_returns_rollback_for_critical_path(): seed step with is_critical_path=1, retry_count=3, "
            "  call analyze_failure, assert decision='rollback' "
            "test_analyze_failure_returns_retry_for_non_critical(): seed step with is_critical_path=0, retry_count=0, "
            "  call analyze_failure, assert decision='retry' "
            "test_apply_decision_retry_increments_retry_count(): create retry decision, apply it, "
            "  assert retry_count incremented on step_execution "
            "test_learn_from_outcome_adjusts_confidence(): create decision with confidence=0.70, "
            "  learn_from_outcome with 'success', assert confidence=0.75 "
            "Run: pytest tests/test_govlift_agentic_recovery.py -v --tb=short"
        ),
    ),
    dict(
        id="gl-p3-11",
        title="Update IQE + manifest + coherence + create Phase 3 feature doc",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-10",
        description=(
            "1. Open tools/iqe/adapters/govlift.py, add 2 collections: "
            "   'recovery_decisions' (5 queries: 'show all rollback decisions', 'list escalated steps', "
            "   'what is the agentic recovery success rate', 'show retry decisions this week', 'find failed recoveries') "
            "   'dependency_graph' (3 queries: 'show workload dependencies', 'list critical dependencies', "
            "   'what workloads have service dependencies') "
            "2. Update tools/manifest/govlift.md: add dependency_mapper.py, agentic_recovery.py; update table count to 17 "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "4. python tools/dx/companion.py --sync --write --json "
            "5. Create docs/features/phase-3-govlift-agentic-recovery.md: "
            "   Overview, New Tables (2), New Modules (2), New Page, API Routes (4), Acceptance Criteria "
            "   Acceptance: failed step triggers recovery decision in <500ms; cascade forecast correctly identifies all downstream-blocked steps "
            "6. pytest tests/test_govlift_agentic_recovery.py -v --tb=short (all must pass)"
        ),
    ),

    # ──────────────────────────────────────────────
    # PHASE 4 — Digital Twin Simulation & Dry Run
    # ──────────────────────────────────────────────
    dict(
        id="gl-p4-01",
        title="Add govlift_simulations and govlift_simulation_steps tables",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p3-11",
        description=(
            "Open tools/govlift/db/init_db.py. Add 2 new tables: "
            "(1) govlift_simulations: id TEXT PK, runbook_id TEXT NOT NULL, scenario_name TEXT, "
            "injection_config_json TEXT DEFAULT '{}', run_count INT DEFAULT 500, "
            "p50_minutes REAL, p95_minutes REAL, p99_minutes REAL, "
            "failure_scenarios_json TEXT DEFAULT '[]', risk_score REAL DEFAULT 0.0, "
            "status TEXT CHECK(status IN ('pending','running','done')), "
            "approved_by TEXT, approved_at TEXT, ran_at TEXT, classification TEXT DEFAULT 'CUI'; "
            "(2) govlift_simulation_steps: id TEXT PK, simulation_id TEXT NOT NULL, step_id TEXT NOT NULL, "
            "simulated_duration_minutes REAL, failure_probability REAL DEFAULT 0.05, "
            "blocked_by_ids_json TEXT DEFAULT '[]', is_critical_path INT DEFAULT 0, "
            "classification TEXT DEFAULT 'CUI'. "
            "All: CREATE TABLE IF NOT EXISTS. "
            "Verify: python tools/govlift/db/init_db.py"
        ),
    ),
    dict(
        id="gl-p4-02",
        title="Write tools/govlift/simulation_engine.py — Part A: build_simulation",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-01",
        description=(
            "Create tools/govlift/simulation_engine.py with Part A: "
            "build_simulation(runbook_id, scenario_config=None) → str (simulation_id): "
            "  - Gets runbook steps from govlift_runbook_steps "
            "  - Applies injection_config overrides: scenario_config dict maps step_id to failure_probability override "
            "  - INSERTs govlift_simulations row with status='pending' "
            "  - INSERTs govlift_simulation_steps for each step: "
            "    simulated_duration_minutes = step.estimated_minutes "
            "    failure_probability = injection_config.get(step_id, 0.05) "
            "    is_critical_path from dependency_mapper.get_critical_path() "
            "  - Returns simulation_id "
            "get_simulation(simulation_id) → dict or None "
            "list_simulations(runbook_id=None) → list of dicts "
            "SIMULATION_STATUSES = ['pending', 'running', 'done'] — also add to constants.py "
            "Module docstring: # CUI // SP-CTI "
            "Verify: python -c 'from tools.govlift.simulation_engine import build_simulation; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p4-03",
        title="Write tools/govlift/simulation_engine.py — Part B: run_monte_carlo + compute_risk_score",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-02",
        description=(
            "Add Part B functions to tools/govlift/simulation_engine.py: "
            "run_monte_carlo(simulation_id, iterations=500) → dict: "
            "  - Sets simulation status='running' "
            "  - Gets all govlift_simulation_steps for simulation_id "
            "  - For each iteration: "
            "    sample step durations: triangular(min=est*0.5, mode=est, max=est*2.0) using random.triangular() "
            "    simulate parallel execution respecting depends_on_ids (topological layers) "
            "    track which steps 'fail' based on failure_probability (random.random() < failure_probability) "
            "    compute total completion time for this iteration "
            "  - Compute p50 = median, p95 = 95th percentile, p99 = 99th percentile from 500 completion times "
            "  - failure_scenarios_json: list top-10 step-combination failures (which steps failed most together) "
            "  - Updates govlift_simulations with p50/p95/p99/failure_scenarios_json, status='done', ran_at=now "
            "  - Returns {'p50': float, 'p95': float, 'p99': float, 'failure_scenarios': list} "
            "compute_risk_score(simulation_id) → float (0–100): "
            "  - For each simulation_step: step_risk = failure_probability * (2.0 if is_critical_path else 1.0) * blast_radius_size "
            "  - Composite = min(100, sum(step_risks) * 10) "
            "  - Updates govlift_simulations.risk_score "
            "  - Returns risk_score "
            "Verify: python -c 'from tools.govlift.simulation_engine import run_monte_carlo; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p4-04",
        title="Write tools/govlift/simulation_engine.py — Part C: export_dry_run_report + execution gate",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-03",
        description=(
            "Add Part C to tools/govlift/simulation_engine.py: "
            "export_dry_run_report(simulation_id) → dict: "
            "  - Generates {'simulation_id', 'runbook_id', 'p50_minutes', 'p95_minutes', 'p99_minutes', "
            "    'risk_score', 'critical_path_steps': [step titles], "
            "    'top_failure_scenarios': [top 5 from failure_scenarios_json], "
            "    'recommendation': 'GO' if risk_score <= threshold else 'NO-GO', "
            "    'markdown_summary': multi-line string with formatted report} "
            "approve_simulation(simulation_id, approved_by) → bool: "
            "  - Updates govlift_simulations.approved_by + approved_at "
            "Then open tools/govlift/blueprint.py, find POST /api/govlift/runbooks/<id>/execute: "
            "  Add risk gate before start_execution(): "
            "  import yaml; cfg = yaml.safe_load(open('args/govlift_config.yaml')); threshold = cfg.get('simulation', {}).get('risk_score_threshold', 70) "
            "  Check if govlift_simulations has approved simulation for runbook_id with risk_score <= threshold "
            "  If gate_enabled and risk_score > threshold and no approved simulation: return jsonify({'success': False, 'error': 'Simulation required', 'risk_score': score}), 409 "
            "Verify: python -c 'from tools.govlift.simulation_engine import export_dry_run_report; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p4-05",
        title="Add simulation API routes to tools/govlift/blueprint.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-04",
        description=(
            "Add 4 simulation API routes to tools/govlift/blueprint.py: "
            "POST /api/govlift/runbooks/<id>/simulate → "
            "  body: {scenario_name, injection_config, iterations} "
            "  calls build_simulation(runbook_id, scenario_config) "
            "  then runs run_monte_carlo(simulation_id, iterations) synchronously "
            "  then compute_risk_score(simulation_id) "
            "  returns {simulation_id, p50, p95, p99, risk_score} → 201 "
            "GET /api/govlift/simulations/<id>/status → get_simulation(id) → 200 "
            "GET /api/govlift/simulations/<id>/report → export_dry_run_report(id) → 200 "
            "POST /api/govlift/simulations/<id>/approve → "
            "  body: {approved_by} "
            "  calls approve_simulation(id, approved_by) → 200 "
            "Also add GET /govlift/simulate page route → renders govlift/simulate.html "
            "Add /govlift/simulate to .claude/commands/start.md Pages: line."
        ),
    ),
    dict(
        id="gl-p4-06",
        title="Create tools/dashboard/templates/govlift/simulate.html",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-05",
        description=(
            "Create tools/dashboard/templates/govlift/simulate.html. "
            "Extends 'base.html'. Title: 'GovLift — Digital Twin Simulation'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout: "
            "- Runbook selector: dropdown of runbooks, select → populates step list below "
            "- Scenario Builder: "
            "  - Scenario name text field "
            "  - Iteration count slider (100–1000, default 500) "
            "  - Failure Injection table: one row per step, step title, failure probability slider (0%–50%) "
            "  - Run Simulation button → POST /api/govlift/runbooks/<id>/simulate, show loading spinner "
            "- Monte Carlo Results (hidden until simulation runs): "
            "  - P50 / P95 / P99 stat cards in minutes "
            "  - Risk Score gauge (0–100, color: green<30, yellow<70, red>=70) "
            "  - Recommendation banner: GO (green) or NO-GO (red) "
            "  - P50/P95/P99 bar chart using Chart.js (load from CDN) "
            "- Critical Path Gantt: horizontal bar chart showing critical path steps with estimated durations "
            "- Top Failure Scenarios table: scenario description, probability, estimated delay "
            "- Approve for Execution button: POST /api/govlift/simulations/<id>/approve, "
            "  enabled only when recommendation=GO or user explicitly overrides "
            "Dark theme, CUI banner."
        ),
    ),
    dict(
        id="gl-p4-07",
        title="Mirror simulate.html to icdev/ and add to nav sidebar",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-06",
        description=(
            "1. Copy tools/dashboard/templates/govlift/simulate.html → icdev/tools/dashboard/templates/govlift/simulate.html "
            "2. Open tools/dashboard/templates/govlift/index.html and add 'Simulation' link to sidebar nav "
            "3. Mirror updated index.html to icdev/tools/dashboard/templates/govlift/index.html "
            "Verify: python -c 'import pathlib; assert pathlib.Path(\"icdev/tools/dashboard/templates/govlift/simulate.html\").exists(); print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p4-08",
        title="Add simulation section to args/govlift_config.yaml and SIMULATION_STATUSES to constants.py",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p4-03",
        description=(
            "1. Open args/govlift_config.yaml. Add simulation section: "
            "simulation: "
            "  risk_score_threshold: 70 "
            "  default_iterations: 500 "
            "  duration_variance_pct: 30 "
            "  gate_enabled: true "
            "  auto_compute_risk: true "
            "Verify valid YAML. "
            "2. Open tools/govlift/constants.py. "
            "Add: SIMULATION_STATUSES = ['pending', 'running', 'done'] "
            "Verify importable: python -c 'from tools.govlift.constants import SIMULATION_STATUSES; print(SIMULATION_STATUSES)'"
        ),
    ),
    dict(
        id="gl-p4-09",
        title="Write pytest unit tests for tools/govlift/simulation_engine.py",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-04",
        description=(
            "Create tests/test_govlift_simulation_engine.py. "
            "test_build_simulation_creates_step_records(): build_simulation on runbook with 4 steps, "
            "  assert govlift_simulation_steps has 4 rows "
            "test_run_monte_carlo_p50_less_than_p95(): run_monte_carlo(simulation_id, iterations=100), "
            "  assert p50 < p95 < p99 "
            "test_run_monte_carlo_updates_simulation_status(): after run, assert status='done' "
            "test_compute_risk_score_returns_0_to_100(): call compute_risk_score, assert 0 <= result <= 100 "
            "test_export_dry_run_report_has_required_keys(): call export_dry_run_report, "
            "  assert all of {p50_minutes, p95_minutes, p99_minutes, risk_score, recommendation} in result "
            "test_export_dry_run_report_go_when_low_risk(): set risk_score=30, assert recommendation='GO' "
            "test_export_dry_run_report_nogo_when_high_risk(): set risk_score=80, assert recommendation='NO-GO' "
            "test_gate_blocks_execute_when_no_approved_simulation(): "
            "  (integration test) run a POST to /api/govlift/runbooks/<high_risk_id>/execute without simulation, "
            "  assert 409 response with 'Simulation required' "
            "Run: pytest tests/test_govlift_simulation_engine.py -v --tb=short"
        ),
    ),
    dict(
        id="gl-p4-10",
        title="Update IQE + manifest + coherence + create Phase 4 feature doc",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-09",
        description=(
            "1. Open tools/iqe/adapters/govlift.py, add 'simulations' collection: "
            "   5 seed queries: 'show simulation results for runbook X', 'list simulations with NO-GO recommendation', "
            "   'what is the p95 execution time for runbook Y', 'show approved simulations', 'find high-risk simulations' "
            "2. Update tools/manifest/govlift.md: add simulation_engine.py; update table count to 19 "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "4. python tools/dx/companion.py --sync --write --json "
            "5. Create docs/features/phase-3-govlift-simulation.md: "
            "   Overview, New Tables (2), New Module, New Page, API Routes (4), Acceptance Criteria "
            "   Acceptance: Monte Carlo (500 iterations) completes in <5s for 50-step runbook; "
            "   gate blocks execution when risk_score>70 without approved simulation "
            "6. pytest tests/test_govlift_simulation_engine.py -v --tb=short (all must pass)"
        ),
    ),

    # ──────────────────────────────────────────────
    # PHASE 5 — Intelligence Layer (Engine Wiring)
    # ──────────────────────────────────────────────
    dict(
        id="gl-p5-01",
        title="Add govlift_intel_signals table to tools/govlift/db/init_db.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p4-10",
        description=(
            "Open tools/govlift/db/init_db.py. Add 1 new table: "
            "govlift_intel_signals: id TEXT PK, source_engine TEXT CHECK(source_engine IN ('creative','research','innovation')), "
            "signal_id TEXT, title TEXT NOT NULL, summary TEXT, category TEXT, "
            "score REAL DEFAULT 0.0, promoted_to_template_id TEXT, reviewed_at TEXT, "
            "hash TEXT UNIQUE, created_at TEXT, classification TEXT DEFAULT 'CUI'. "
            "CREATE TABLE IF NOT EXISTS. "
            "The hash column prevents duplicate signal imports (SHA256 of source_engine+signal_id). "
            "Verify: python tools/govlift/db/init_db.py"
        ),
    ),
    dict(
        id="gl-p5-02",
        title="Write tools/govlift/intel_bridge.py — sync signals from Creative/Research/Innovation engines",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-01",
        description=(
            "Create tools/govlift/intel_bridge.py with: "
            "import hashlib, json "
            "from tools.db.storage import get_connection "
            "_signal_hash(source_engine, signal_id): return hashlib.sha256(f'{source_engine}:{signal_id}'.encode()).hexdigest()[:16] "
            "sync_creative_signals() → int (new signals inserted): "
            "  SELECT id, title, summary, category, composite_score FROM creative_pain_points "
            "  WHERE category IN ('migration_automation', 'federal_it_modernization') AND composite_score >= 0.60 "
            "  For each: compute hash, INSERT INTO govlift_intel_signals if hash not exists "
            "  Returns count of new inserts "
            "sync_research_signals() → int: "
            "  SELECT id, title, summary, category FROM research_challenges WHERE vertical='federal_migration' "
            "  INSERT with source_engine='research', score=0.7 (default) "
            "sync_innovation_signals() → int: "
            "  SELECT id, title, summary, category, score FROM innovation_signals WHERE category LIKE '%migration%' "
            "  INSERT with source_engine='innovation' "
            "promote_to_template(signal_id) → str (template_id): "
            "  Gets govlift_intel_signals row "
            "  Calls marketplace.py internally to create draft template: "
            "  from tools.govlift.marketplace import _create_template_from_signal "
            "  (add _create_template_from_signal to marketplace.py: creates govlift_playbook_templates row with is_certified=0) "
            "  Updates govlift_intel_signals.promoted_to_template_id and reviewed_at "
            "  Returns template_id "
            "All functions: handle missing source tables gracefully (try/except, return 0 on OperationalError) "
            "Module docstring: # CUI // SP-CTI "
            "Verify: python -c 'from tools.govlift.intel_bridge import sync_creative_signals; print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p5-03",
        title="Add intelligence API routes and page route to tools/govlift/blueprint.py",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-02",
        description=(
            "Add 3 intelligence API routes to tools/govlift/blueprint.py: "
            "GET /api/govlift/intel/signals → "
            "  SELECT * FROM govlift_intel_signals, supports ?source=creative|research|innovation and ?reviewed=true|false "
            "  pagination: ?page=1&per_page=50 "
            "  → 200 with {signals: list, total: int, page: int} "
            "POST /api/govlift/intel/signals/<id>/promote → "
            "  calls promote_to_template(signal_id) → 200 with {template_id: str} "
            "POST /api/govlift/intel/sync → "
            "  calls sync_creative_signals() + sync_research_signals() + sync_innovation_signals() "
            "  → 200 with {creative: N, research: N, innovation: N, total_new: N} "
            "Also add GET /govlift/intel page route → renders govlift/intel.html "
            "Add /govlift/intel to .claude/commands/start.md Pages: line."
        ),
    ),
    dict(
        id="gl-p5-04",
        title="Create tools/dashboard/templates/govlift/intel.html — intelligence signal viewer",
        task_type="build",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-03",
        description=(
            "Create tools/dashboard/templates/govlift/intel.html. "
            "Extends 'base.html'. Title: 'GovLift — Intelligence Signals'. "
            "Include IQE widget (canvas='govlift'). "
            "Layout (3-panel): "
            "LEFT PANEL — Source Filter Tabs: "
            "  Tab buttons: All | Creative Engine | Research Engine | Innovation Engine "
            "  Click tab → JS filters signal cards by source_engine "
            "CENTER PANEL — Signal Cards: "
            "  Each card: title, source badge (Creative/Research/Innovation with distinct colors), "
            "  score badge (0.0–1.0 as percentage), category chip, summary (first 150 chars), "
            "  'Promote to Template' button → POST /api/govlift/intel/signals/<id>/promote, "
            "  show success toast with template_id "
            "  Load from GET /api/govlift/intel/signals on page load "
            "RIGHT PANEL — Template Draft Preview: "
            "  Shown when signal is promoted: displays auto-generated template name, step count, NIST controls "
            "  links to /govlift/marketplace to view full template "
            "HEADER STAT CARDS: "
            "  Signals This Week | Promoted to Templates | Competitor Pain Points | Research Findings "
            "SYNC button (top right): POST /api/govlift/intel/sync, show results in toast "
            "Dark theme, CUI banner."
        ),
    ),
    dict(
        id="gl-p5-05",
        title="Mirror intel.html to icdev/, add to nav, add govlift_intel_signals to APPEND_ONLY_TABLES",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-04",
        description=(
            "1. Copy tools/dashboard/templates/govlift/intel.html → icdev/tools/dashboard/templates/govlift/intel.html "
            "2. Open tools/dashboard/templates/govlift/index.html and add 'Intelligence' link to sidebar nav "
            "3. Mirror updated index.html to icdev/tools/dashboard/templates/govlift/index.html "
            "4. Open .claude/hooks/pre_tool_use.py and add 'govlift_intel_signals' to APPEND_ONLY_TABLES "
            "   (signals are audit-grade competitive intelligence, must not be edited) "
            "5. Verify: python -c 'import pathlib; assert pathlib.Path(\"icdev/tools/dashboard/templates/govlift/intel.html\").exists(); print(\"OK\")'"
        ),
    ),
    dict(
        id="gl-p5-06",
        title="Update tools/iqe/adapters/govlift.py with intel_signals collection",
        task_type="build",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p5-03",
        description=(
            "Open tools/iqe/adapters/govlift.py. Add 'intel_signals' collection with 5 seed queries: "
            "'what competitor pain points were found this week', "
            "'which signals were promoted to templates', "
            "'show research findings for federal migration', "
            "'what innovation signals scored above 0.7', "
            "'summarize compliance-related intelligence signals' "
            "SQL: SELECT * FROM govlift_intel_signals WHERE ... "
            "Add graceful handling for missing govlift_intel_signals table (try/except). "
            "Also add 5 seed query files to context/iqe/queries/govlift/ for intel_signals collection."
        ),
    ),
    dict(
        id="gl-p5-07",
        title="Write pytest unit tests for tools/govlift/intel_bridge.py",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-02",
        description=(
            "Create tests/test_govlift_intel_bridge.py. "
            "Setup: seed test rows in creative_pain_points, research_challenges, innovation_signals tables if they exist, "
            "otherwise mock the DB queries. "
            "test_sync_creative_signals_filters_score(): seed 2 pain points: one score=0.55, one score=0.70, "
            "  call sync_creative_signals(), assert only the 0.70 signal is inserted (score>=0.60 filter) "
            "test_sync_research_signals_filters_vertical(): seed challenge with vertical='generic' and one with 'federal_migration', "
            "  assert only federal_migration is inserted "
            "test_promote_to_template_creates_uncertified_template(): seed a govlift_intel_signals row, "
            "  call promote_to_template(signal_id), assert govlift_playbook_templates has row with is_certified=0 "
            "test_duplicate_signal_hash_is_skipped(): call sync_creative_signals() twice, "
            "  assert total insert count = first_call_count (second call inserts 0 duplicates) "
            "test_missing_source_table_returns_zero(): if creative_pain_points doesn't exist, "
            "  sync_creative_signals() returns 0 without raising "
            "Run: pytest tests/test_govlift_intel_bridge.py -v --tb=short"
        ),
    ),
    dict(
        id="gl-p5-08",
        title="Update govlift.md manifest + update Phase 5 feature docs",
        task_type="chore",
        priority="medium",
        status="backlog",
        depends_on_task_id="gl-p5-07",
        description=(
            "1. Update tools/manifest/govlift.md: "
            "   Add intel_bridge.py entry with sync_creative_signals, sync_research_signals, sync_innovation_signals, promote_to_template "
            "   Add govlift_intel_signals table entry "
            "   Update total table count to 20 "
            "   Update page count to 14 (was 6, added 8 new pages) "
            "   Update API route count to ~50 "
            "2. Create docs/features/phase-3-govlift-intelligence-layer.md: "
            "   Overview, New Table, New Module, New Page, API Routes (3), Acceptance Criteria "
            "   Acceptance: Intel dashboard shows signals from ≥2 engines; "
            "   at least 1 pain point promotes to draft template per run "
            "3. Verify manifest is complete and accurate."
        ),
    ),
    dict(
        id="gl-p5-09",
        title="Run full GovLift test suite and fix any failures",
        task_type="test",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-08",
        description=(
            "Run the full GovLift test suite: "
            "pytest tests/test_govlift*.py -v --tb=short "
            "Fix any test failures found. Common failure patterns: "
            "- Import errors: check that all new modules are importable "
            "- DB schema errors: ensure conftest.py MINIMAL_ICDEV_SCHEMA has all govlift tables "
            "  (add any missing govlift table schemas to tests/conftest.py) "
            "- Missing fixture data: add minimal seed rows in test setup "
            "All tests must pass before proceeding to gl-p5-10. "
            "If more than 3 tests fail, fix them all before marking this task done."
        ),
    ),
    dict(
        id="gl-p5-10",
        title="Final coherence check + companion sync + verify all 14 govlift pages reachable",
        task_type="chore",
        priority="high",
        status="backlog",
        depends_on_task_id="gl-p5-09",
        description=(
            "Final validation for the complete GovLift enhancement: "
            "1. python tools/workflow/coherence_checker.py --all --fix --gate "
            "   Fix any coherence errors. Must exit 0. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. Verify all 14 GovLift pages are reachable — check tools/govlift/blueprint.py for all page routes: "
            "   /govlift (index), /govlift/workloads, /govlift/waves, /govlift/executor, "
            "   /govlift/stig, /govlift/audit, /govlift/runbooks, /govlift/marketplace, "
            "   /govlift/compliance, /govlift/recovery, /govlift/simulate, /govlift/intel, "
            "   /govlift/runbooks/<id>, /govlift/runbooks/<id>/execute "
            "   Assert all 14 routes exist in blueprint.py. "
            "4. python tools/testing/health_check.py --json "
            "   Assert no FAIL status for govlift components. "
            "5. Output a final summary: total tables (20), pages (14), API routes (~50), modules (8). "
            "If any step fails, fix the root cause and re-run."
        ),
    ),
]


def seed(dry_run: bool = False) -> None:
    conn = get_connection()
    existing = {r["id"] for r in conn.execute("SELECT id FROM kanban_tasks").fetchall()}
    inserted = skipped = 0

    for t in TASKS:
        if t["id"] in existing:
            skipped += 1
            continue

        completed_at = _NOW if t.get("status") == "done" else None

        if dry_run:
            print(f"  DRY [{t.get('status', 'backlog'):8}] {t['id']} — {t['title'][:65]}")
        else:
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority,
                    status, scheduled_at, depends_on_task_id,
                    executor_type, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"],
                    t["title"],
                    t.get("description", ""),
                    t.get("task_type", "build"),
                    t.get("priority", "medium"),
                    t.get("status", "backlog"),
                    t.get("scheduled_at"),
                    t.get("depends_on_task_id"),
                    "claude_cli",
                    _NOW,
                    _NOW,
                ),
            )
            conn.commit()
            inserted += 1
            print(f"  [{t.get('status', 'backlog'):8}] {t['id']} — {t['title'][:65]}")

    print(f"\n  Total tasks: {len(TASKS)}  Inserted: {inserted}  Skipped (exist): {skipped}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed GovLift enhancement kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
