#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: Workflow Studio — People, Tools & Process Integration.

4 epics, 10 tasks:
  wfs-schema-*  — YAML schema extension + linter update
  wfs-api-*     — Studio API endpoints (roles + doc-templates)
  wfs-studio-*  — 3-tier node model: JS + CSS + config modal
  wfs-vv-*      — V&V gate

Run: python tools/db/seeds/seed_wfs_plan.py
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
        task_id,
        title,
        description,
        task_type,
        priority,
        "scheduled",
        NOW,
        "claude_cli",
        "wfs_plan",
        depends_on,
        NOW,
        NOW,
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
    # EPIC SCHEMA — YAML schema extension + linter
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wfs-schema-01",
        "Create args/workflow_templates/README.md — 3-node-type schema spec",
        (
            "Create args/workflow_templates/README.md documenting the extended YAML schema. "
            "Cover all five new optional fields that apply to each step node: "
            "  node_type: tool | human | approval  (default: tool) "
            "  role: <string>  — role label from wf_team_members (tool: ignored; human: owner; approval: approver group) "
            "  human_required: true | false  (only meaningful when node_type: human) "
            "  approval_policy: any_one | all | majority  (only for node_type: approval) "
            "  doc_template: <string>  — wf_doc_templates.name (optional SOP/checklist) "
            "Include a full annotated example showing a 4-step flow: "
            "  tool node -> human gate -> approval gate -> tool node. "
            "Also update tools/studio/template_linter.py to validate node_type values: "
            "  allowed = {None, 'tool', 'human', 'approval'} "
            "  if a step has node_type not in allowed, report as a lint error. "
            "Only touch README.md and template_linter.py."
        ),
        task_type="build",
        priority="high",
    ),
    _task(
        "wfs-schema-02",
        "Add node_type: tool to all 14 existing workflow template YAMLs + add sample human/approval gates to 2 templates",
        (
            "Step 1 — Backward-compat marker: add 'node_type: tool' to every step in "
            "all 14 files under args/workflow_templates/. This is explicit but changes no behavior. "
            "Step 2 — Sample human/approval gates: "
            "  In args/workflow_templates/requirements_intake.yaml, insert two new steps "
            "  between 'baselined' and the end of the workflow: "
            "    - id: stakeholder_review "
            "      name: Stakeholder Review "
            "      node_type: human "
            "      role: program_manager "
            "      human_required: true "
            "      doc_template: sop_requirements_review "
            "      depends_on: [baselined] "
            "    - id: crb_approval "
            "      name: CRB Approval Gate "
            "      node_type: approval "
            "      approval_policy: all "
            "      role: change_review_board "
            "      depends_on: [stakeholder_review] "
            "  In args/workflow_templates/ato_acceleration.yaml, after the final 'poam' step add: "
            "    - id: iso_review "
            "      name: ISO/ISSO Review "
            "      node_type: human "
            "      role: isso "
            "      human_required: true "
            "      doc_template: sop_ato_review "
            "      depends_on: [poam] "
            "    - id: ao_approval "
            "      name: Authorizing Official Approval "
            "      node_type: approval "
            "      approval_policy: any_one "
            "      role: authorizing_official "
            "      depends_on: [iso_review] "
            "Run python tools/studio/template_linter.py --check after to confirm 0 errors."
        ),
        task_type="build",
        priority="high",
        depends_on="wfs-schema-01",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC API — Studio roles + doc-templates endpoints
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wfs-api-01",
        "Add GET /api/studio/roles to tools/studio/studio.py",
        (
            "In tools/studio/studio.py add a new route: "
            "  @bp.route('/api/studio/roles') "
            "  def studio_roles(): "
            "The route queries SELECT DISTINCT role_label FROM wf_team_members ORDER BY role_label. "
            "If the table doesn't exist or returns 0 rows, fall back to DEFAULT_ROLES: "
            "  ['architect', 'authorizing_official', 'change_review_board', "
            "   'chief_engineer', 'configuration_manager', 'isso', 'program_manager', "
            "   'security_engineer', 'systems_engineer', 'test_lead'] "
            "Return JSON: {roles: [<string>, ...], source: 'db' | 'default'}. "
            "Use get_connection(), degrade gracefully on any DB error. "
            "Only touch studio.py."
        ),
        task_type="build",
        priority="high",
    ),
    _task(
        "wfs-api-02",
        "Add GET /api/studio/doc-templates to tools/studio/studio.py",
        (
            "In tools/studio/studio.py add a second new route: "
            "  @bp.route('/api/studio/doc-templates') "
            "  def studio_doc_templates(): "
            "Query: SELECT doc_template_id, name, doc_type, canvas_type FROM wf_doc_templates "
            "WHERE is_system = 1 OR is_ai_reference = 1 ORDER BY name. "
            "If table missing or 0 rows, return empty list with source: 'none'. "
            "Return JSON: {templates: [{id, name, doc_type, canvas_type}, ...], source: 'db' | 'none'}. "
            "Use get_connection(), degrade gracefully. "
            "Only touch studio.py."
        ),
        task_type="build",
        priority="medium",
        depends_on="wfs-api-01",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC STUDIO — JS + CSS + config modal (3-tier node model)
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wfs-studio-01",
        "Add node_type field to JS node model + YAML import/export + renderNode data attribute",
        (
            "In tools/dashboard/static/js/workflow-studio.js: "
            "1. Extend the node object to include node_type: 'tool' (default). "
            "   When building a node from a YAML step, read step.node_type || 'tool'. "
            "2. In exportYAML(): emit node_type, role, human_required, approval_policy, "
            "   doc_template fields per node (omit null/undefined/false values). "
            "3. In importYAML(): read those five fields into the node object. "
            "4. In renderNode(): add data-node-type attribute on the node div: "
            "   el.dataset.nodeType = n.node_type || 'tool'. "
            "5. Also add role/approval_policy as data attributes for the config modal to read. "
            "Mirror all changes to icdev/tools/dashboard/static/js/workflow-studio.js. "
            "No visual changes in this task — just data model."
        ),
        task_type="build",
        priority="critical",
    ),
    _task(
        "wfs-studio-02",
        "CSS + JS — 3 visual node styles: tool (purple), human gate (teal), approval gate (amber)",
        (
            "In tools/dashboard/static/css/studio.css: "
            "Add node type variant styles that activate via [data-node-type] attribute: "
            "  .wf-node[data-node-type='human'] { "
            "    border-color: #2dd4bf; "
            "    background: rgba(45,212,191,0.08); "
            "  } "
            "  .wf-node[data-node-type='human'] .wf-node__icon::before { content: '\\1F464'; } "
            "  .wf-node[data-node-type='human'] .wf-node__badge { "
            "    background: rgba(45,212,191,0.15); color: #2dd4bf; "
            "  } "
            "  .wf-node[data-node-type='approval'] { "
            "    border-color: #f59e0b; "
            "    background: rgba(245,158,11,0.08); "
            "  } "
            "  .wf-node[data-node-type='approval'] .wf-node__icon::before { content: '\\1F512'; } "
            "  .wf-node[data-node-type='approval'] .wf-node__badge { "
            "    background: rgba(245,158,11,0.15); color: #f59e0b; "
            "  } "
            "In renderNode() JS: set the badge text to show role (human) or policy (approval) "
            "when those fields are set. Example: human node badge = 'Human | <role>'; "
            "approval node badge = 'Approval | <policy>'. "
            "Mirror to icdev/. Only touch studio.css and workflow-studio.js."
        ),
        task_type="build",
        priority="critical",
        depends_on="wfs-studio-01",
    ),
    _task(
        "wfs-studio-03",
        "Extend node config modal — role / approval_policy / doc_template fields",
        (
            "In tools/dashboard/static/js/workflow-studio.js, extend openNodeConfig() and "
            "saveNodeConfig() to handle the three new node types. "
            "In the config modal HTML (inside workflow_studio.html or injected by JS): "
            "Add a 'Node Type' <select> with options: Tool / Human Gate / Approval Gate. "
            "When 'Human Gate' is selected, show: "
            "  - Role <select> populated by fetch('/api/studio/roles') "
            "  - Human Required <checkbox> (default: true) "
            "  - Document Template <select> populated by fetch('/api/studio/doc-templates') "
            "    (each option shows name + doc_type) "
            "When 'Approval Gate' is selected, show: "
            "  - Approval Policy <select>: Any One / All / Majority "
            "  - Approver Role <select> (same roles endpoint) "
            "When 'Tool' is selected, show only the existing tool/description fields. "
            "saveNodeConfig() writes all visible fields back to the node object. "
            "Fetch calls are lazy (only when modal opens) and cached in a module-level variable. "
            "Mirror to icdev/. Touch workflow_studio.html and workflow-studio.js only."
        ),
        task_type="build",
        priority="critical",
        depends_on="wfs-studio-02",
    ),
    _task(
        "wfs-studio-04",
        "Final mirror pass — sync all studio JS + CSS changes to icdev/",
        (
            "Confirm that tools/dashboard/static/js/workflow-studio.js and "
            "tools/dashboard/static/css/studio.css are byte-for-byte identical to their "
            "icdev/ counterparts: "
            "  icdev/tools/dashboard/static/js/workflow-studio.js "
            "  icdev/tools/dashboard/static/css/studio.css "
            "Run a diff and patch any divergences. "
            "Also verify tools/dashboard/templates/studio/workflow_studio.html is mirrored "
            "to icdev/tools/dashboard/templates/studio/workflow_studio.html. "
            "This is a pure sync/chore task — no new logic."
        ),
        task_type="chore",
        priority="medium",
        depends_on="wfs-studio-03",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC VV — Validation + companion + coherence + smoke test
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "wfs-vv-01",
        "Linter gate + manifest + companion sync + coherence — wfs project sign-off",
        (
            "1. python tools/studio/template_linter.py --check --gate "
            "   All 16 templates (14 original + 2 updated) must report 0 errors. "
            "2. Add wfs project entry to tools/manifest/studio.md shard: "
            "   document the 3 node types, the 5 new YAML fields, the 2 new API routes. "
            "3. python tools/dx/companion.py --sync --write --json "
            "4. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore",
        priority="high",
        depends_on="wfs-studio-04",
    ),
    _task(
        "wfs-vv-02",
        "V&V smoke test — load requirements_intake template, verify all 3 node types render correctly",
        (
            "Using Playwright MCP or manual browser at http://localhost:5050/studio/workflows: "
            "1. Open Template Library → load 'Requirements Intake' template. "
            "2. Verify tool nodes render with purple border. "
            "3. Verify 'Stakeholder Review' (human gate) renders with teal border + person icon. "
            "4. Verify 'CRB Approval Gate' (approval gate) renders with amber border + lock icon. "
            "5. Click 'Stakeholder Review' node → open config modal → "
            "   confirm Role dropdown is populated (not empty) from /api/studio/roles. "
            "6. Press Validate button → confirm toast says 'Workflow valid — N steps, N connections'. "
            "7. Export YAML → confirm node_type, role, approval_policy fields are present. "
            "Document pass/fail for each check. If any check fails, fix the root cause before "
            "marking this task done."
        ),
        task_type="run",
        priority="critical",
        depends_on="wfs-vv-01",
    ),
]


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    for task in TASKS:
        cur.execute(INSERT_SQL, task)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    schema = sum(1 for t in TASKS if t[0].startswith("wfs-schema"))
    api    = sum(1 for t in TASKS if t[0].startswith("wfs-api"))
    studio = sum(1 for t in TASKS if t[0].startswith("wfs-studio"))
    vv     = sum(1 for t in TASKS if t[0].startswith("wfs-vv"))

    print(f"[seed_wfs_plan] done — {inserted} inserted, {skipped} skipped (conflict)")
    print(f"  Epic schema (YAML schema + linter):     wfs-schema-01..02  ({schema} tasks)")
    print(f"  Epic api    (roles + doc-tmpl routes):  wfs-api-01..02     ({api} tasks)")
    print(f"  Epic studio (JS + CSS + config modal):  wfs-studio-01..04  ({studio} tasks)")
    print(f"  Epic vv     (linter + smoke test):      wfs-vv-01..02      ({vv} tasks)")
    print()
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
