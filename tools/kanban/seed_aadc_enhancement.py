# CUI // SP-CTI
"""Seed script — AADC Enhancement (aadc-p1/p2/p3).

Three-phase enhancement initiative for the Agentic AI Design Canvas:
  Phase 1 (p1): Canvas Parity — undo/redo, multi-select, copy/paste, draw.io + PDF + CSV
                export, compliance legend, version diff viewer
  Phase 2 (p2): Ecosystem Wiring — Genesis reflex, MCP registry, activity feed, fine-tuning
                linkage, Kanban back-sync
  Phase 3 (p3): AADC-Unique — safety redundancy graph, multi-agent coordination matrix,
                model provenance chain, agent behavior simulation

Run:
    cd C:/Users/schuo/Downloads/ICDev && python tools/kanban/seed_aadc_enhancement.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.kanban.task_factory import create_tasks  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

TASKS: list[dict] = [

    # ── PHASE 1: CANVAS PARITY ────────────────────────────────────────────────

    {
        "id": "aadc-p1-01",
        "title": "Undo/redo history stack (Ctrl+Z / Ctrl+Y) in canvas.html",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add a JS history array (max 50 states). Each state = deep JSON clone of "
            "current nodes+edges via JSON.parse(JSON.stringify(...)). "
            "Snapshot triggers: addNode(), deleteNode(), moveNode (on drag-end), "
            "addEdge(), deleteEdge(). "
            "Functions: pushHistory(), undo() — pop state and call restoreState(snapshot), "
            "redo() — reapply from a redo stack. "
            "restoreState(snapshot) clears #nodes-layer + #edges-layer children and "
            "re-renders all nodes/edges from snapshot data. "
            "Bind: document keydown Ctrl+Z → undo(), Ctrl+Y / Ctrl+Shift+Z → redo(). "
            "Add toolbar buttons: ↩ Undo / ↪ Redo (disabled when stack empty). "
            "No new Python files. No DB change."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "aadc-p1-02",
        "title": "Multi-select: rubber-band drag-select + Shift+click in canvas.html",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "State: selectedNodes = Set of node ids. "
            "Rubber-band: on canvas mousedown (not on a node, not Space), draw a "
            "<div id='select-rect'> with absolute positioning + dashed border. "
            "On mousemove update width/height. On mouseup compute overlap with every "
            "node rect (canvas coords) and add to selectedNodes. "
            "Shift+click on node: toggle that node in selectedNodes (don't deselect others). "
            "Plain click on canvas: clear selectedNodes. "
            "Visual: add CSS class 'selected' to .node divs in selectedNodes (blue ring). "
            "Bulk move: when dragging any node in selectedNodes, move all others by the "
            "same delta. "
            "Delete: if Delete key pressed and selectedNodes.size > 0, bulk-delete. "
            "No Python files. No DB change."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-01",
    },
    {
        "id": "aadc-p1-03",
        "title": "Copy/Paste nodes (Ctrl+C / Ctrl+V) with offset in canvas.html",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "State: _clipboard = [] array of node snapshots (deep copies). "
            "Ctrl+C: if selectedNodes.size > 0, deep-clone those node objects into "
            "_clipboard (strip id, mark .copied=true). "
            "Ctrl+V: for each node in _clipboard, create a new node with new id "
            "(uuid or Date.now()+index), offset position by +20px x/y, add to nodes map "
            "and render. Clear _clipboard after paste (single paste). "
            "Push to history after paste. "
            "Show brief 'Copied N nodes' toast notification (auto-dismiss 2s). "
            "No Python files. No DB change."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-02",
    },
    {
        "id": "aadc-p1-04",
        "title": "Export to draw.io XML from canvas.html (client-side serializer)",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add function doExportDrawio() invoked from the AADC menu (add 'Export draw.io' "
            "item alongside existing Export JSON / Export SVG). "
            "Serializes current nodes+edges to draw.io mxGraph XML format: "
            "<mxGraphModel><root><mxCell id='0'/><mxCell id='1' parent='0'/> then one "
            "<mxCell> per node (vertex=1, geometry x/y/width/height) and one per edge "
            "(edge=1, source/target). "
            "Node label = node.label. Node style = shape based on node.type: "
            "LLM→rounded=1, Tool→shape=mxgraph.bpmn.task, RAG→shape=cylinder. "
            "Downloads as <design_id>.drawio via Blob URL. "
            "No Python files. No DB change."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-03",
    },
    {
        "id": "aadc-p1-05",
        "title": "PDF export: tools/agentic_ai_canvas/export_pdf.py + blueprint route",
        "description": (
            "New file tools/agentic_ai_canvas/export_pdf.py. "
            "Function generate_pdf(design_id: str, nodes: list, edges: list) -> bytes. "
            "Uses reportlab (already in requirements.txt) to generate a letter-size PDF: "
            "page 1 = title + metadata (design_id, timestamp, node count, edge count), "
            "page 2 = nodes table (id, label, type, description), "
            "page 3+ = edges table (source label → target label, relationship). "
            "Apply CUI header/footer on every page via classification_manager.py. "
            "Add POST /agentic-ai/canvas/<design_id>/export-pdf to "
            "tools/agentic_ai_canvas/blueprint.py: accepts JSON body {nodes, edges}, "
            "returns application/pdf with Content-Disposition: attachment. "
            "Add 'Export PDF' menu item in canvas.html that POSTs current nodes+edges "
            "and triggers download. "
            "No new DB tables."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-04",
    },
    {
        "id": "aadc-p1-06",
        "title": "CSV export (nodes + edges) from canvas.html (client-side)",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add function doExportCSV() invoked from AADC menu. "
            "Generates two CSV sections separated by a blank line: "
            "Section 1 NODES — columns: id, label, type, x, y, description, autonomy_level. "
            "Section 2 EDGES — columns: id, source_id, source_label, target_id, target_label, "
            "label, relationship_type. "
            "Combines into a single CSV string, downloads as <design_id>-data.csv via Blob URL. "
            "No Python files. No DB change."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-05",
    },
    {
        "id": "aadc-p1-07",
        "title": "Compliance legend overlay in canvas.html + agentic-canvas.css",
        "description": (
            "Files: canvas.html + tools/dashboard/static/css/agentic-canvas.css. "
            "Add a collapsible 'Compliance Legend' panel (bottom-right of canvas container). "
            "Panel button in toolbar: '§ Legend'. Clicking toggles visibility. "
            "Panel content: for the currently loaded design, compute per-framework node coverage: "
            "NIST AI RMF (Govern/Map/Measure/Manage), OWASP LLM Top 10, MITRE ATLAS tactics. "
            "Coverage = count of nodes whose node_type maps to that control area (use "
            "JS object COMPLIANCE_MAP that maps type→[frameworks]). "
            "Show as colored pills: green ≥ 80%, yellow 40-79%, red < 40%. "
            "Also show a mini-legend of node type colors at the bottom. "
            "CSS in agentic-canvas.css: .compliance-legend panel styles."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-06",
    },
    {
        "id": "aadc-p1-08",
        "title": "DB migration: aadc_design_versions table for version history",
        "description": (
            "Create tools/db/migrations/085_aadc_versions.sql. "
            "Table aadc_design_versions: "
            "  id SERIAL PRIMARY KEY, "
            "  design_id TEXT NOT NULL REFERENCES aadc_designs(id) ON DELETE CASCADE, "
            "  version_num INT NOT NULL, "
            "  label TEXT, "
            "  snapshot_json JSONB NOT NULL, "
            "  created_by TEXT, "
            "  created_at TIMESTAMPTZ DEFAULT NOW(). "
            "UNIQUE(design_id, version_num). "
            "Add graceful table-existence guard in migration runner. "
            "No Python logic changes — DB DDL only."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-07",
    },
    {
        "id": "aadc-p1-09",
        "title": "Version diff viewer backend: tools/agentic_ai_canvas/version_diff.py + routes",
        "description": (
            "New file tools/agentic_ai_canvas/version_diff.py. "
            "Functions: "
            "  save_version(design_id, snapshot_json, label=None) -> version_num: "
            "    reads max version_num for design_id, increments, inserts row. "
            "  list_versions(design_id) -> list[dict]: returns all versions sorted desc. "
            "  diff_versions(design_id, v1, v2) -> dict: compares snapshots, returns "
            "    {added_nodes, removed_nodes, changed_nodes, added_edges, removed_edges} "
            "    where each item has id+label. "
            "Add routes to blueprint.py: "
            "  GET  /agentic-ai/canvas/<design_id>/versions → list_versions JSON "
            "  POST /agentic-ai/canvas/<design_id>/versions → save_version, body {label} "
            "  GET  /agentic-ai/canvas/<design_id>/versions/diff?v1=N&v2=M → diff_versions JSON. "
            "Uses get_connection(). No new tables (uses aadc-p1-08 migration)."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-08",
    },
    {
        "id": "aadc-p1-10",
        "title": "Version diff viewer UI in canvas.html (version panel + diff modal)",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add toolbar button: '⊞ Versions'. Clicking opens a right-side drawer listing "
            "versions (version_num, label, created_at). "
            "Each version row has: [Restore] button and a checkbox for diff selection. "
            "Select two checkboxes + click 'Compare' → opens a modal showing diff results: "
            "  + Added nodes (green rows), - Removed nodes (red rows), ~ Changed (yellow). "
            "  Same for edges. "
            "'Save Version' button in drawer opens a small input (optional label) and "
            "POSTs to /agentic-ai/canvas/<design_id>/versions. "
            "Auto-save version on every explicit Ctrl+S / 'Save Design' action. "
            "No new Python files beyond aadc-p1-09."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-09",
    },
    {
        "id": "aadc-p1-11",
        "title": "V&V Phase 1: Playwright E2E + companion sync + coherence gate",
        "description": (
            "Playwright E2E tests for all Phase 1 features: "
            "  1. Open /agentic-ai/canvas/<test_id>, drop 2 nodes, verify Ctrl+Z removes last. "
            "  2. Verify Ctrl+Y restores it. "
            "  3. Drag-select 2 nodes, verify both get CSS 'selected' class. "
            "  4. Ctrl+C + Ctrl+V, verify 2 new nodes appear offset +20px. "
            "  5. Open AADC menu, click 'Export draw.io', verify download triggered. "
            "  6. Click 'Export CSV', verify download triggered. "
            "  7. Click '§ Legend', verify compliance panel visible with pill colors. "
            "  8. Click '⊞ Versions', verify version drawer opens. "
            "  9. Click 'Save Version', verify version_num increments. "
            " 10. Select 2 versions + Compare, verify diff modal appears. "
            "Run: python tools/dx/companion.py --sync --write --json "
            "     python tools/workflow/coherence_checker.py --all --fix --gate"
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-10",
    },

    # ── PHASE 2: ECOSYSTEM WIRING ─────────────────────────────────────────────

    {
        "id": "aadc-p2-01",
        "title": "DB migration: aadc_design_events table for activity feed",
        "description": (
            "Create tools/db/migrations/086_aadc_events.sql. "
            "Table aadc_design_events: "
            "  id SERIAL PRIMARY KEY, "
            "  design_id TEXT NOT NULL, "
            "  event_type TEXT NOT NULL CHECK (event_type IN "
            "    ('save','export_json','export_svg','export_drawio','export_pdf','export_csv',"
            "     'assess','version_save','node_add','node_delete','edge_add','edge_delete',"
            "     'simulation_run')), "
            "  actor TEXT, "
            "  metadata_json JSONB, "
            "  created_at TIMESTAMPTZ DEFAULT NOW(). "
            "Add graceful table-existence guard. "
            "No Python logic — DDL only."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p1-11",
    },
    {
        "id": "aadc-p2-02",
        "title": "Activity feed emitter: emit aadc_design_events on save/export/assess",
        "description": (
            "New file tools/agentic_ai_canvas/events.py. "
            "Function emit_event(design_id, event_type, actor=None, metadata=None). "
            "Writes one row to aadc_design_events using get_connection(). "
            "Call emit_event() from: "
            "  blueprint.py save route (event_type='save'), "
            "  export_pdf.py (event_type='export_pdf'), "
            "  version_diff.py save_version (event_type='version_save'). "
            "Also call from canvas.html via fetch POST to new route: "
            "  POST /agentic-ai/canvas/<design_id>/events — body {event_type, metadata}. "
            "Add that route to blueprint.py. "
            "Hook client-side export-JSON, export-SVG, export-drawio, export-CSV functions "
            "to POST the event before downloading. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-01",
    },
    {
        "id": "aadc-p2-03",
        "title": "Genesis reflex consumer: tools/genesis/reflexes/aadc_reflex.py",
        "description": (
            "New file tools/genesis/reflexes/aadc_reflex.py. "
            "Function run(payload: dict, ctx) → dict. "
            "Triggered by Genesis on aadc_design_events where event_type IN "
            "('assess','simulation_run'). "
            "Actions: "
            "  1. Load latest design snapshot from aadc_designs table. "
            "  2. Run compliance scoring (reuse assessment engine from existing "
            "     tools/agentic_ai_canvas/assessment.py if exists, else score per "
            "     NIST/OWASP/ATLAS node coverage). "
            "  3. Write a memory_entry (type='insight') via memory_write.py if score changed "
            "     by ≥ 5 points from previous run. "
            "  4. If critical gap detected (any framework < 40%), create a Kanban task "
            "     suggestion (status='suggested', task_type='chore'). "
            "Register in tools/genesis/reflex_registry.py under key 'aadc_compliance'. "
            "Uses get_connection(). COOLDOWN_HOURS = 4."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-02",
    },
    {
        "id": "aadc-p2-04",
        "title": "MCP registry auto-populate: sync AADC agent nodes to tool_registry",
        "description": (
            "New file tools/agentic_ai_canvas/mcp_sync.py. "
            "Function sync_design_to_mcp(design_id: str) → dict. "
            "Reads all nodes of type IN ('LLM Agent','MCP Server','Tool') from the design. "
            "For each, upserts a row in mcp_tool_registry (tools/mcp/tool_registry.py): "
            "  name = node.label, source = 'aadc:<design_id>', capabilities_json = "
            "  {'description': node.description, 'node_type': node.type, 'design_id': design_id}. "
            "Call sync_design_to_mcp() from blueprint.py save route after emit_event(). "
            "Add /agentic-ai/canvas/<design_id>/sync-mcp POST route for manual trigger. "
            "Show sync result count in canvas.html as a brief toast ('Synced N tools to MCP'). "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-03",
    },
    {
        "id": "aadc-p2-05",
        "title": "Fine-tuning dashboard linkage: surface AADC assessment scores as fine-tuning signals",
        "description": (
            "New file tools/agentic_ai_canvas/ft_linkage.py. "
            "Function export_assessment_as_ft_signal(design_id: str) → dict. "
            "Reads latest assessment result for the design (from aadc_assessments or "
            "run assessment inline). "
            "Creates or upserts a row in fine_tuning_datasets with: "
            "  source='aadc', source_id=design_id, "
            "  input_text='Design: <design_id> | Framework: <framework> | Score: <score>', "
            "  expected_output='Improve coverage for: <gap list>', "
            "  quality_signal=score/100.0. "
            "Add GET /agentic-ai/canvas/<design_id>/ft-link route that calls this and "
            "returns {datasets_created: N}. "
            "Add 'Send to Fine-Tuning' item in AADC menu → fetch this route + toast result. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-04",
    },
    {
        "id": "aadc-p2-06",
        "title": "Kanban back-sync: display linked task status badge on canvas nodes",
        "description": (
            "Files: canvas.html + tools/agentic_ai_canvas/blueprint.py. "
            "Add GET /agentic-ai/canvas/<design_id>/kanban-status route. "
            "For each node in the design that has metadata.kanban_task_id set, "
            "query kanban_tasks.status for that id. "
            "Returns {node_id: {task_id, status, title}} map. "
            "In canvas.html, after renderNodes(), fetch /kanban-status and for each "
            "node with a task_id, overlay a small badge (bottom-right of node div): "
            "  ✓ green = done, ⟳ blue = in_progress, ⊡ gray = scheduled, ⚠ red = failed. "
            "Badge click opens the Kanban board in a new tab. "
            "Properties panel: when a node is selected, show 'Linked Task:' row with "
            "status badge + input to set/change kanban_task_id. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-05",
    },
    {
        "id": "aadc-p2-07",
        "title": "V&V Phase 2: integration tests + companion sync + coherence gate",
        "description": (
            "Playwright E2E tests for Phase 2 features: "
            "  1. Save a design → verify aadc_design_events row inserted (event_type='save'). "
            "  2. Export draw.io → verify event row (event_type='export_drawio'). "
            "  3. Trigger Genesis reflex manually → verify memory_entry or kanban suggestion. "
            "  4. Add LLM Agent node, save → verify mcp_tool_registry row upserted. "
            "  5. Click 'Send to Fine-Tuning' menu → verify fine_tuning_datasets row. "
            "  6. Create a node, set kanban_task_id via properties panel, save → "
            "     verify badge renders. "
            "Run: python tools/dx/companion.py --sync --write --json "
            "     python tools/workflow/coherence_checker.py --all --fix --gate"
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-06",
    },

    # ── PHASE 3: AADC-UNIQUE FEATURES ────────────────────────────────────────

    {
        "id": "aadc-p3-01",
        "title": "DB migration: aadc_safety_graphs + aadc_agent_simulations tables",
        "description": (
            "Create tools/db/migrations/087_aadc_p3.sql. "
            "Table aadc_safety_graphs: "
            "  id SERIAL PRIMARY KEY, design_id TEXT NOT NULL, "
            "  analysis_json JSONB NOT NULL, "
            "  gaps_count INT DEFAULT 0, redundancies_count INT DEFAULT 0, "
            "  created_at TIMESTAMPTZ DEFAULT NOW(). "
            "Table aadc_agent_simulations: "
            "  id SERIAL PRIMARY KEY, design_id TEXT NOT NULL, "
            "  scenario_name TEXT NOT NULL, "
            "  scenario_json JSONB NOT NULL, "
            "  result_json JSONB, "
            "  status TEXT DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')), "
            "  created_at TIMESTAMPTZ DEFAULT NOW(), "
            "  completed_at TIMESTAMPTZ. "
            "Add graceful table-existence guards. DDL only."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p2-07",
    },
    {
        "id": "aadc-p3-02",
        "title": "Safety redundancy graph backend: tools/agentic_ai_canvas/safety_graph.py",
        "description": (
            "New file tools/agentic_ai_canvas/safety_graph.py. "
            "Function analyze_safety(design_id: str, nodes: list, edges: list) -> dict. "
            "Algorithm: "
            "  1. Build adjacency graph from edges. "
            "  2. Identify 'safety nodes': type IN ('Human-in-Loop','Guardrail','Monitor'). "
            "  3. For each non-safety node, check if any path to it passes through a safety node "
            "     (reachability via DFS). Gap = non-safety node with no safety node upstream. "
            "  4. Redundancy = safety node that covers an already-fully-covered subgraph "
            "     (all downstream nodes already have another safety path). "
            "Returns {gaps: [{node_id, label, type}], redundancies: [{node_id, label}], "
            "  safety_coverage_pct: float}. "
            "Writes result to aadc_safety_graphs. "
            "Add POST /agentic-ai/canvas/<design_id>/safety-graph route to blueprint.py. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-01",
    },
    {
        "id": "aadc-p3-03",
        "title": "Safety redundancy graph UI: overlay panel in canvas.html",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add toolbar button: '🛡 Safety'. Clicking POSTs current nodes+edges to "
            "/agentic-ai/canvas/<design_id>/safety-graph and shows results in a "
            "right-side drawer: "
            "  - Safety Coverage: <pct>% (progress bar, red<50/yellow<80/green≥80) "
            "  - Gaps section: list of unsafe nodes (clicking pans canvas to that node "
            "    and highlights it with a red outline). "
            "  - Redundancies section: list of redundant safety nodes (yellow outline). "
            "  Node highlighting: add CSS class 'safety-gap' (red dashed border) and "
            "  'safety-redundant' (yellow border) to node divs after analysis. "
            "Clear highlights on next analyze or when drawer is closed."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-02",
    },
    {
        "id": "aadc-p3-04",
        "title": "Multi-agent coordination matrix backend: tools/agentic_ai_canvas/coord_matrix.py",
        "description": (
            "New file tools/agentic_ai_canvas/coord_matrix.py. "
            "Function build_matrix(nodes: list, edges: list) -> dict. "
            "Filters nodes to agent types: "
            "  IN ('LLM Agent','Orchestrator','Supervisor','Planner','Executor','Tool'). "
            "Builds an NxN matrix where matrix[i][j] = list of edges between agent_i and "
            "agent_j (both directions). Each edge entry: {id, label, direction}. "
            "Also computes: "
            "  - in_degree, out_degree per agent. "
            "  - Bottleneck agents: in_degree > mean + 1.5*std. "
            "  - Isolated agents: in_degree + out_degree == 0. "
            "Returns {agents: [{id, label, type, in_degree, out_degree}], "
            "  matrix: [[{edges}]], bottlenecks: [id], isolated: [id]}. "
            "Add GET /agentic-ai/canvas/<design_id>/coord-matrix route to blueprint.py. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-03",
    },
    {
        "id": "aadc-p3-05",
        "title": "Multi-agent coordination matrix UI: modal in canvas.html",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add toolbar button: '⊞ Matrix'. Clicking fetches "
            "/agentic-ai/canvas/<design_id>/coord-matrix and opens a modal. "
            "Modal contains an HTML table: rows = agents, columns = agents. "
            "Cell (i,j) shows edge count between agent_i ↔ agent_j (or blank if 0). "
            "Click a non-zero cell → mini tooltip listing edge labels. "
            "Below the table: "
            "  Bottlenecks: colored red — <label> (in:N, out:M). "
            "  Isolated agents: colored gray — <label>. "
            "  Top connector: agent with highest total degree. "
            "No Python changes beyond aadc-p3-04."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-04",
    },
    {
        "id": "aadc-p3-06",
        "title": "Model provenance chain: tracker backend + UI in properties panel",
        "description": (
            "New file tools/agentic_ai_canvas/model_provenance.py. "
            "Function get_provenance_chain(design_id: str) -> list[dict]. "
            "For each node of type IN ('LLM Agent','LLM','Fine-tuned Model'): "
            "  Reads node.metadata.model_id, .base_model, .fine_tune_run_id from design JSON. "
            "  If fine_tune_run_id set, joins to fine_tuning_runs table to get "
            "  base_model, dataset_source, training_date. "
            "Returns list of {node_id, label, model_id, base_model, provider, "
            "  fine_tuned: bool, fine_tune_date, dataset_source}. "
            "Add GET /agentic-ai/canvas/<design_id>/model-provenance to blueprint.py. "
            "In canvas.html properties panel: when selected node type is LLM/Agent, "
            "add 'Model Provenance' section showing model_id input, base_model input, "
            "fine_tune_run_id input (with lookup button), and a read-only 'Chain' display "
            "that fetches and renders the provenance on demand. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-05",
    },
    {
        "id": "aadc-p3-07",
        "title": "Agent behavior simulation engine: tools/agentic_ai_canvas/simulation.py",
        "description": (
            "New file tools/agentic_ai_canvas/simulation.py. "
            "Function run_simulation(design_id: str, scenario: dict) -> dict. "
            "scenario keys: name, steps (list of {source_agent_id, message, expects_response_from}). "
            "Simulation algorithm (no LLM call — deterministic trace): "
            "  1. Build adjacency map from design edges. "
            "  2. For each step, trace message flow: starting node → follow outbound edges "
            "     → collect reachable agents in topological order. "
            "  3. Record path as [{node_id, label, type, latency_est_ms (random 50-500)}]. "
            "  4. Flag if any HITL node is on the path (human_in_loop_triggered=true). "
            "  5. Flag if any path exceeds 10 hops (runaway_loop=true). "
            "Returns {scenario_name, steps_traced: [path], human_in_loop_triggered, "
            "  runaway_loop, total_latency_est_ms}. "
            "Writes result to aadc_agent_simulations. "
            "Add POST /agentic-ai/canvas/<design_id>/simulate route to blueprint.py. "
            "Uses get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-06",
    },
    {
        "id": "aadc-p3-08",
        "title": "Agent simulation UI: run-sim panel in canvas.html",
        "description": (
            "File: tools/dashboard/templates/agentic_ai_canvas/canvas.html only. "
            "Add toolbar button: '▶ Simulate'. Clicking opens a right-side panel. "
            "Panel has: "
            "  Scenario name input, "
            "  Steps list (add step: source agent select, message text, expected-from select). "
            "  [Run Simulation] button → POSTs to /agentic-ai/canvas/<design_id>/simulate. "
            "Results panel: for each step, show the traced path as an animated flow "
            "(briefly highlight each node in the path sequentially with CSS animation 300ms/hop). "
            "Summary: total latency estimate, HITL triggered (yellow banner), "
            "runaway loop detected (red banner). "
            "Previous simulations: list from aadc_agent_simulations (GET route) "
            "with re-run and view-results options. "
            "No new Python files beyond aadc-p3-07."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-07",
    },
    {
        "id": "aadc-p3-09",
        "title": "V&V Phase 3: Playwright E2E + feature docs + companion sync + coherence gate",
        "description": (
            "Playwright E2E tests for Phase 3 features: "
            "  1. Click '🛡 Safety', verify safety drawer opens with coverage %. "
            "  2. Verify gap nodes get red border overlay. "
            "  3. Click '⊞ Matrix', verify coordination matrix modal shows NxN table. "
            "  4. Click a non-zero cell, verify edge tooltip appears. "
            "  5. Select an LLM Agent node, verify Model Provenance section in properties. "
            "  6. Click '▶ Simulate', add a step, run → verify path highlights animate. "
            "  7. Verify result saved in aadc_agent_simulations table. "
            "Create docs/features/phase-aadc-p3-unique.md (CUI marked). "
            "Run: python tools/dx/companion.py --sync --write --json "
            "     python tools/workflow/coherence_checker.py --all --fix --gate"
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "aadc-p3-08",
    },
]

# Extra dependency edges beyond depends_on_task_id (none needed — chain is linear)
EXTRA_DEPS: list[tuple[str, str]] = []


def seed() -> None:
    # Fold any EXTRA_DEPS (extra junction parents) into each task's depends_on
    # list; the factory writes both the scalar parent and the junction rows.
    extra_by_task: dict[str, list[str]] = {}
    for task_id, dep_id in EXTRA_DEPS:
        extra_by_task.setdefault(task_id, []).append(dep_id)
    tasks = []
    for t in TASKS:
        t = dict(t)
        if t["id"] in extra_by_task:
            t["depends_on"] = list(t.get("depends_on", [])) + extra_by_task[t["id"]]
        tasks.append(t)

    report = create_tasks("aadc", tasks, strict=False, register_project=False)
    print(report.summary())


if __name__ == "__main__":
    seed()
