"""Seed wex Kanban tasks into PostgreSQL via get_connection()."""
# CUI // SP-CTI
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

TASKS = [
    # ── EP7: Canvas Bridge ────────────────────────────────────────────────────
    {
        "id": "wex-ep7-01",
        "title": "canvas_bridge.py — canvas-to-workflow context mapper",
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on": None,
        "description": (
            "Create tools/studio/canvas_bridge.py.\n\n"
            "Maps canvas_id (idc/ndc/sdc/bdc/pdc/odc/ddc/qdc/mdc/aadc/aimc/ohc) to a default\n"
            "workflow dict {description, category, steps[]} so POST /from-canvas can pre-populate\n"
            "a workflow without Ollama.\n\n"
            "CANVAS_WORKFLOW_MAP covers all 12 canvases. Each entry has 3-5 steps with realistic\n"
            "tool paths, node_type (tool/human/approval), depends_on chain, timeout=300, required=True.\n"
            "Step IDs: step_1, step_2, ... (must be unique within workflow).\n\n"
            "Functions:\n"
            "  get_canvas_workflow_template(canvas_id: str) -> dict  # fallback if unknown\n"
            "  list_canvas_ids() -> list[str]\n\n"
            "No Flask imports. Pure data module."
        ),
    },
    {
        "id": "wex-ep7-02",
        "title": "API route — POST /api/studio/workflows/from-canvas",
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "depends_on": "wex-ep7-01",
        "description": (
            "Add to tools/dashboard/api/studio.py:\n\n"
            "  @studio_bp.route('/workflows/from-canvas', methods=['POST'])\n"
            "  def create_workflow_from_canvas():\n\n"
            "Accepts JSON: {canvas_id, context_label (optional)}.\n"
            "Calls get_canvas_workflow_template(canvas_id) from tools/studio/canvas_bridge.\n"
            "Appends context_label to description if provided.\n"
            "Generates UUID workflow_id, converts template dict to YAML, calls save_workflow().\n"
            "Returns: {workflow_id, name, redirect: '/studio/workflows?load={id}'}.\n"
            "400 if canvas_id unknown. 503 if canvas_bridge import fails."
        ),
    },
    {
        "id": "wex-ep7-03",
        "title": "Shared partial — templates/includes/workflow_trigger_btn.html",
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "depends_on": "wex-ep7-01",
        "description": (
            "Create tools/dashboard/templates/includes/workflow_trigger_btn.html.\n\n"
            "Jinja2 partial. Caller sets wf_canvas_id and wf_context_label then includes this file.\n"
            "Renders a button that POSTs to /api/studio/workflows/from-canvas and opens the\n"
            "returned redirect URL in a new tab.\n"
            "Inline <script> with sendToWorkflowStudio(canvasId, contextLabel).\n"
            "Mirror to icdev/ directory if it exists."
        ),
    },
    # ── EP8: Canvas Wiring ────────────────────────────────────────────────────
    {
        "id": "wex-ep8-01",
        "title": "Wire workflow trigger into idc + ndc + sdc canvas templates",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep7-03",
        "description": (
            "Add workflow trigger partial to the main page template of:\n"
            "  infra_canvas/ (idc), network/ (ndc), security_canvas/ (sdc)\n\n"
            "Insert near action bar:\n"
            "  {%% set wf_canvas_id = 'idc' %%}\n"
            "  {%% set wf_context_label = item.name|default('') %%}\n"
            "  {%% include 'includes/workflow_trigger_btn.html' %%}\n\n"
            "Find the primary template (rendered by main list/index route). Do not break layout."
        ),
    },
    {
        "id": "wex-ep8-02",
        "title": "Wire workflow trigger into bdc + pdc + odc canvas templates",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep7-03",
        "description": (
            "Same pattern as wex-ep8-01 for:\n"
            "  boundary_canvas/ (bdc), pipeline/ (pdc), observability_canvas/ (odc)"
        ),
    },
    {
        "id": "wex-ep8-03",
        "title": "Wire workflow trigger into ddc + qdc + mdc canvas templates",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep7-03",
        "description": (
            "Same pattern as wex-ep8-01 for:\n"
            "  data_canvas/ (ddc), qdc_canvas/ (qdc), migration_canvas/ (mdc)"
        ),
    },
    {
        "id": "wex-ep8-04",
        "title": "Wire workflow trigger into aadc + aimc + ohc canvas templates",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep7-03",
        "description": (
            "Same pattern as wex-ep8-01 for:\n"
            "  agentic_ai_canvas/ (aadc), aiml_canvas/ (aimc), ops_hub/ (ohc)"
        ),
    },
    # ── EP9: Template Library ─────────────────────────────────────────────────
    {
        "id": "wex-ep9-01",
        "title": "Seed 18 YAML workflow templates in context/workflow_templates/",
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on": None,
        "description": (
            "Create context/workflow_templates/ and seed 18 YAML files.\n\n"
            "12 canvas-specific: {canvas_id}_workflow.yaml for each of the 12 canvases.\n"
            "6 general: fedramp_ato_pipeline.yaml, cmmc_assessment.yaml,\n"
            "           cicd_security_gate.yaml, govcon_proposal.yaml,\n"
            "           incident_response.yaml, data_lineage_audit.yaml\n\n"
            "Each file: description, category, tags[], author, steps[].\n"
            "3-5 steps each. Use real tool paths from tools/ directory.\n"
            "Human/approval nodes: tool='', role=isso|reviewer|approver.\n"
            "Step IDs: step_1, step_2, ..."
        ),
    },
    {
        "id": "wex-ep9-02",
        "title": "API route — GET /api/studio/workflows/templates",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep9-01",
        "description": (
            "Add to tools/dashboard/api/studio.py:\n\n"
            "  @studio_bp.route('/workflows/templates', methods=['GET'])\n"
            "  def list_workflow_templates():\n\n"
            "Reads *.yaml from context/workflow_templates/ (relative to project root).\n"
            "Returns: {templates: [{id, name, description, category, tags, author, steps_count, yaml}],\n"
            "          total, builtin, community: 0, categories: {cat: count}}.\n"
            "Handles missing directory gracefully (returns empty list, no 500)."
        ),
    },
    {
        "id": "wex-ep9-03",
        "title": "Template Library tab — JS card grid + Load button",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep9-02",
        "description": (
            "Implement in workflow-studio-exec.js:\n\n"
            "StudioWF.loadTemplates(): fetches /api/studio/workflows/templates,\n"
            "  updates stat counters (tpl-builtin-count, tpl-community-count, tpl-category-count),\n"
            "  renders card grid in #wf-template-grid with category badge, steps count, name,\n"
            "  description, tags, and Load button.\n\n"
            "StudioWF.installTemplate(t): switches to Editor tab, calls doImportYAML(t.yaml),\n"
            "  sets #wf-name to t.name, shows toast.\n\n"
            "Wire into switchTab(): call loadTemplates() when tab=='templates' (first visit).\n"
            "Helper _categoryColor(cat) maps category to badge color class."
        ),
    },
    # ── EP10: Saved Workflows + Run History ───────────────────────────────────
    {
        "id": "wex-ep10-01",
        "title": "Saved Workflows tab — JS rendering with Edit/Run/Delete actions",
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on": None,
        "description": (
            "Implement in workflow-studio-exec.js:\n\n"
            "StudioWF.loadSavedWorkflows(): fetches GET /api/studio/workflows,\n"
            "  updates #wf-saved-count, renders card grid in #wf-saved-grid\n"
            "  (category badge, steps count, name, description, Edit/Run/Delete buttons).\n\n"
            "StudioWF.loadWorkflow(id, runAfter=false): switches to Editor tab,\n"
            "  fetches GET /api/studio/workflows/{id}, calls doImportYAML(w.yaml),\n"
            "  sets #wf-name, _currentWorkflowId, shows Run+Script buttons.\n"
            "  If runAfter=true: setTimeout run(), 400ms.\n\n"
            "StudioWF.deleteWorkflow(id, name): confirm, DELETE, refresh, toast.\n\n"
            "Wire into switchTab(): call loadSavedWorkflows() when tab=='saved'."
        ),
    },
    {
        "id": "wex-ep10-02",
        "title": "Run History tab — JS rendering with step-breakdown modal",
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on": None,
        "description": (
            "Complete Run History tab in workflow-studio-exec.js:\n\n"
            "StudioWF.loadRunHistory(): fetches GET /api/studio/workflows/runs,\n"
            "  renders rows in #wf-runs-body (workflow name, status badge, steps done/total,\n"
            "  duration, started_at, Detail button).\n\n"
            "StudioWF.showRunDetail(runId): complete the existing partial implementation.\n"
            "  Fetches GET /api/studio/workflows/runs/{runId}.\n"
            "  Opens #wf-run-detail-modal, sets title, renders step table in #wf-run-detail-body\n"
            "  (step name, status badge, duration, stdout preview 200 chars).\n\n"
            "_runStatusBadge(status): colored HTML badge per status.\n\n"
            "Wire into switchTab(): call loadRunHistory() when tab=='runs'."
        ),
    },
    # ── EP11: Manifest + Companion + V&V ──────────────────────────────────────
    {
        "id": "wex-ep11-01",
        "title": "Manifest — update tools/manifest/studio.md with new modules",
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep9-02",
        "description": (
            "Update tools/manifest/studio.md (create if missing).\n\n"
            "Add entries for:\n"
            "  tools/studio/workflow_runner.py - per-run SSE execution engine\n"
            "  tools/studio/workflow_chat.py - chat-to-YAML via Ollama/Qwen3\n"
            "  tools/studio/canvas_bridge.py - canvas-to-workflow template mapper\n"
            "  context/workflow_templates/ - 18 pre-built YAML templates\n"
            "  tools/dashboard/templates/includes/workflow_trigger_btn.html - reusable partial\n\n"
            "Follow existing manifest shard format (name, path, purpose, key functions)."
        ),
    },
    {
        "id": "wex-ep11-02",
        "title": "Companion sync + coherence check",
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "depends_on": "wex-ep11-01",
        "description": (
            "Run mandatory post-implementation sync:\n\n"
            "  python tools/dx/companion.py --sync --write --json\n"
            "  python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "Fix any coherence failures before marking done.\n"
            "Log to .tmp/wex-companion-sync.log"
        ),
    },
    {
        "id": "wex-ep11-03",
        "title": "Playwright V&V — full workflow lifecycle E2E test",
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "depends_on": "wex-ep11-02",
        "description": (
            "Playwright E2E verification of full Workflow Studio lifecycle.\n\n"
            "Scenario 1 — Chat->Generate->Save->Run:\n"
            "  Navigate /studio/workflows, open AI Chat, send 'ATO pipeline - FIPS categorization,\n"
            "  ISSO approval, SSP generation', wait for YAML (30s), Apply, verify nodes,\n"
            "  Save, verify Run button, click Run, verify SSE progress + step events, wait for completion.\n\n"
            "Scenario 2 — Template Library:\n"
            "  Click Template Library tab, verify cards>0, Load any card, verify canvas populates.\n\n"
            "Scenario 3 — Run History:\n"
            "  Click Run History tab, verify run from scenario 1 appears, click Detail,\n"
            "  verify modal with step breakdown.\n\n"
            "Scenario 4 — Canvas Trigger (pick any canvas):\n"
            "  Navigate to a canvas page, verify Workflow button present, click it,\n"
            "  verify redirect to /studio/workflows?load=...\n\n"
            "Screenshot each step: playwright/screenshots/wex_e2e_{step}.png.\n"
            "Fix root cause of any failure before marking done."
        ),
    },
]


def main() -> None:
    with get_connection() as conn:
        inserted = 0
        skipped = 0
        for t in TASKS:
            existing = conn.execute(
                "SELECT 1 FROM kanban_tasks WHERE id = %s", (t["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, depends_on_task_id, executor_type, dispatch_source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    t["id"], t["title"], t.get("description", ""),
                    t["task_type"], t["priority"], t["status"],
                    NOW, t.get("depends_on"), "claude_cli", "wex-seed",
                ),
            )
            inserted += 1

        print(f"Inserted: {inserted}  Skipped: {skipped}")

        rows = conn.execute(
            "SELECT id, status FROM kanban_tasks WHERE id LIKE 'wex-%' ORDER BY id"
        ).fetchall()
        print(f"Total wex tasks in DB: {len(rows)}")
        for r in rows:
            print(f"  {r['id']} | {r['status']}")


if __name__ == "__main__":
    main()
