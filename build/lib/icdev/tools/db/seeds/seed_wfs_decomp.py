#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: Workflow Studio — Node Decomposition (composite type + drill-down navigation).

Epic decomp, 5 tasks:
  wfs-decomp-01  — YAML schema: composite node_type + sub_steps field + recursive linter
  wfs-decomp-02  — JS data model: sub_steps in node object + import/export
  wfs-decomp-03  — Studio UX: canvas navigation stack + breadcrumb + Decompose button
  wfs-decomp-04  — Sub-canvas full Studio instance reusing all existing node types
  wfs-decomp-05  — V&V: decompose red_team + owasp_llm in AI Security Assessment

Run: python tools/db/seeds/seed_wfs_decomp.py
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
        "wfs_decomp",
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

    _task(
        "wfs-decomp-01",
        "YAML schema: add composite node_type + sub_steps field + recursive linter validation",
        (
            "Extend args/workflow_templates/README.md (created in wfs-schema-01) to document "
            "the 4th node type: composite. "
            "  node_type: composite "
            "  sub_steps: [...]   # array of full step objects, same schema as top-level steps "
            "                     # sub_steps may themselves be composite (arbitrary nesting) "
            "  tool: <string>     # optional: the rolled-up tool that runs the whole sub-graph "
            "Rules: "
            "  - composite nodes may have depends_on at the parent level (normal DAG wiring) "
            "  - sub_steps form their own independent DAG with their own depends_on references "
            "  - sub_step IDs are scoped to their parent (no collision with top-level IDs) "
            "  - a composite node with 0 sub_steps is a lint error "
            "Update tools/studio/template_linter.py: "
            "  - validate node_type: composite has at least 1 sub_step "
            "  - recurse into sub_steps and run the full analysis (isolated, disconnected, "
            "    cycles) on the sub-DAG independently "
            "  - report sub-DAG errors prefixed with parent node name: "
            "    'red_team.sub_steps: 2 isolated node(s)' "
            "CLI: python tools/studio/template_linter.py --check still works on any .yaml file. "
            "Only touch README.md and template_linter.py."
        ),
        task_type="build",
        priority="high",
    ),
    _task(
        "wfs-decomp-02",
        "JS data model: sub_steps in node object + YAML import/export for composite nodes",
        (
            "In tools/dashboard/static/js/workflow-studio.js: "
            "1. Node object gains: sub_steps: [] (default empty array). "
            "2. importYAML(): when a step has sub_steps, parse them recursively into "
            "   node.sub_steps as an array of node objects (same shape, with their own "
            "   id/label/tool/node_type/sub_steps). "
            "3. exportYAML(): for composite nodes, emit the sub_steps array under the parent "
            "   step. Omit sub_steps key entirely when empty. "
            "4. renderNode(): composite nodes get a small expand icon button (⊞) in the "
            "   top-right corner of the node card. The button calls drillInto(node.id). "
            "   Add data-has-sub-steps='true' attribute so CSS can style it. "
            "5. The Decompose action is also available via right-click context menu on any "
            "   node: 'Decompose node' — calls drillInto(node.id). If node.sub_steps is "
            "   empty, the action instead initialises an empty sub-canvas (the user builds "
            "   the sub-DAG from scratch). "
            "Mirror all changes to icdev/tools/dashboard/static/js/workflow-studio.js. "
            "Only touch workflow-studio.js (both copies)."
        ),
        task_type="build",
        priority="critical",
        depends_on="wfs-decomp-01",
    ),
    _task(
        "wfs-decomp-03",
        "Studio UX: canvas navigation stack + breadcrumb bar + Decompose/Back controls",
        (
            "This is the core drill-down task. In workflow-studio.js: "
            ""
            "1. Module-level navigation stack: "
            "   let canvasStack = [];  // [{nodes, edges, name, parentNodeId}, ...] "
            ""
            "2. drillInto(nodeId): "
            "   - push {nodes:[...nodes], edges:[...edges], name:currentName, parentNodeId:nodeId} "
            "     onto canvasStack "
            "   - set nodes = node.sub_steps (or [] if empty) "
            "   - set edges = [] (rebuild from sub_steps depends_on) "
            "   - set currentName = parentName + ' › ' + node.label "
            "   - re-render canvas (nodesEl, edgesSvg, counts) "
            "   - renderBreadcrumb() "
            "   - when sub_steps was empty: show empty-state with hint "
            "     'You are inside [node name]. Add steps to build its sub-workflow.' "
            ""
            "3. drillOut(): "
            "   - pop from canvasStack "
            "   - restore nodes, edges, currentName "
            "   - sync sub_steps back to the parent node: "
            "     const parentNode = nodes.find(n => n.id === frame.parentNodeId); "
            "     parentNode.sub_steps = [...current sub-nodes]; "
            "   - re-render canvas "
            "   - renderBreadcrumb() "
            ""
            "4. renderBreadcrumb(): "
            "   - render a breadcrumb bar above the canvas when canvasStack.length > 0 "
            "   - each crumb is clickable and calls drillOutTo(depth) "
            "   - format: 'AI Security Assessment  ›  Red Team  ›  Adversarial Tests' "
            "   - when canvasStack is empty, hide the breadcrumb bar "
            ""
            "5. In the toolbar, the Back button (already present as undo stub) is repurposed "
            "   to call drillOut() when canvasStack.length > 0, with label 'Back to <parent>'. "
            "   When canvasStack is empty it remains as undo stub. "
            ""
            "In tools/dashboard/static/css/studio.css: "
            "   .wf-breadcrumb — horizontal bar above canvas, dark background, crumb separators "
            "   .wf-breadcrumb__crumb — clickable, truncates at max-width "
            "   .wf-node[data-has-sub-steps='true'] — subtle expand icon overlay "
            ""
            "Mirror JS + CSS to icdev/. "
            "Touch workflow-studio.js, studio.css, and their icdev/ mirrors only."
        ),
        task_type="build",
        priority="critical",
        depends_on="wfs-decomp-02",
    ),
    _task(
        "wfs-decomp-04",
        "Sub-canvas full feature parity: all node types + validate + connect work inside sub-workflows",
        (
            "Verify and fix any feature gaps when operating inside a drilled-down sub-canvas: "
            ""
            "1. All 4 node types work in sub-canvas: tool, human, approval, composite "
            "   (recursive nesting — you can decompose a node inside a sub-workflow). "
            ""
            "2. Validate button works inside sub-canvas: runs Union-Find + cycle check "
            "   on the sub-DAG nodes/edges (not the parent). "
            "   Toast shows: 'Sub-workflow valid — N steps, N connections (inside: <name>)'. "
            ""
            "3. Port drag / manual connect works inside sub-canvas (connectState, mousemove, "
            "   mouseup all operate on the current nodes/edges regardless of depth). "
            "   No code change needed here if drillInto() correctly replaces the module-level "
            "   nodes/edges arrays — verify this is the case. "
            ""
            "4. Save / exportYAML at top level serialises the full nested structure including "
            "   all sub_steps at every depth. "
            ""
            "5. 'Add step' toolbar action adds to the current canvas level (sub or top). "
            ""
            "6. Delete node at sub-canvas level only deletes from the sub-steps array, "
            "   never from the parent. "
            ""
            "Fix any issues found. Mirror to icdev/. "
            "Only touch workflow-studio.js (both copies) and studio.css if needed."
        ),
        task_type="build",
        priority="high",
        depends_on="wfs-decomp-03",
    ),
    _task(
        "wfs-decomp-05",
        "V&V: decompose red_team + owasp_llm in AI Security Assessment; verify any-template drill-down",
        (
            "Using Playwright MCP or manual browser at http://localhost:5050/studio/workflows: "
            ""
            "Test 1 — red_team decomposition (AI Security Assessment template): "
            "  1. Load 'AI Security Assessment' template. "
            "  2. Click the ⊞ expand icon on the 'ATLAS Red Team' node. "
            "  3. Verify breadcrumb shows: 'AI Security Assessment  ›  ATLAS Red Team'. "
            "  4. Verify empty sub-canvas with hint message is shown. "
            "  5. Add 3 tool nodes: 'Model Theft', 'Indirect Prompt Injection', 'Findings Synthesis'. "
            "  6. Connect Model Theft → Findings Synthesis, Indirect Prompt Injection → Findings Synthesis. "
            "  7. Press Validate — confirm 'Sub-workflow valid — 3 steps, 2 connections'. "
            "  8. Click Back — confirm return to AI Security Assessment canvas. "
            "  9. Export YAML — confirm red_team step contains sub_steps array with 3 entries. "
            ""
            "Test 2 — owasp_llm decomposition: "
            "  1. Click ⊞ on 'OWASP LLM Top 10' node. "
            "  2. Add 3 nodes: 'LLM01 Prompt Injection', 'LLM06 Sensitive Info', 'Triage'. "
            "  3. Connect both checks → Triage. Validate. Back. "
            ""
            "Test 3 — any-template: "
            "  1. Load 'Requirements Intake' template. "
            "  2. Decompose any node. Verify drill-down and back work. "
            "  3. Load 'MBSE Digital Thread'. Decompose any node. Verify. "
            ""
            "Test 4 — recursive nesting: "
            "  1. Inside a sub-canvas, add a composite node and drill into it (depth 2). "
            "  2. Verify breadcrumb shows 3 levels. "
            "  3. Back twice returns to top. "
            "  4. Export YAML at top level — verify nested sub_steps structure. "
            ""
            "Document pass/fail per check. Fix any root-cause failures before marking done."
        ),
        task_type="run",
        priority="critical",
        depends_on="wfs-decomp-04",
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

    print(f"[seed_wfs_decomp] done — {inserted} inserted, {skipped} skipped (conflict)")
    print("  wfs-decomp-01  YAML schema: composite + sub_steps + recursive linter")
    print("  wfs-decomp-02  JS data model: sub_steps + import/export + expand icon")
    print("  wfs-decomp-03  UX: navigation stack + breadcrumb + drillInto/drillOut")
    print("  wfs-decomp-04  Sub-canvas parity: all node types + validate + connect")
    print("  wfs-decomp-05  V&V: red_team + owasp_llm + any-template + recursive nesting")
    print()
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
