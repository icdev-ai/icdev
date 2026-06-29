#!/usr/bin/env python3
# CUI // SP-CTI
"""Cascade-to-Simulation Bridge.

Adapts BFS cascade analysis for program dependency propagation.
Traces multi-level impacts through a simulation Knowledge Graph
built from SysML elements, compliance controls, supply chain vendors,
and digital thread traces.

Key differences from FathomDesk cascade_engine.py:
  - Nodes are program elements (components, controls, vendors), not tickers
  - Timing model uses sprints/PIs, not market months
  - Impact maps to 6 simulation dimensions, not price direction
  - Auto-generates simulation modifications from cascade discoveries

Usage:
    python tools/simulation/cascade_bridge.py --project proj-123 --trigger "add AWS" --json
    python tools/simulation/cascade_bridge.py --project proj-123 --node node-id --depth 5 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Program-specific relationship types and their dimension impact
CASCADE_RELATIONSHIPS = {
    "DEPENDS_ON": {"direction": "downstream", "dimensions": ["architecture", "schedule"]},
    "IMPLEMENTS": {"direction": "downstream", "dimensions": ["compliance"]},
    "SATISFIES": {"direction": "downstream", "dimensions": ["compliance"]},
    "SUPPLIES_TO": {"direction": "downstream", "dimensions": ["supply_chain"]},
    "REQUIRES_RESOURCE": {"direction": "upstream", "dimensions": ["supply_chain", "cost"]},
    "CONSTRAINED_BY": {"direction": "upstream", "dimensions": ["schedule", "risk"]},
    "ENABLES": {"direction": "downstream", "dimensions": ["architecture", "cost"]},
    "DEPLOYS_ON": {"direction": "downstream", "dimensions": ["architecture", "supply_chain"]},
    "REFERENCES": {"direction": "downstream", "dimensions": ["compliance"]},
    "RUNS": {"direction": "downstream", "dimensions": ["architecture"]},
    "PROTECTS": {"direction": "downstream", "dimensions": ["compliance", "risk"]},
}

# Timing model: sprint-based delays per cascade level
LEVEL_TIMING = {
    1: {"label": "Immediate", "sprints": 0},
    2: {"label": "Same sprint", "sprints": 1},
    3: {"label": "Next sprint", "sprints": 2},
    4: {"label": "Same PI", "sprints": 3},
    5: {"label": "Next PI", "sprints": 5},
    6: {"label": "2 PIs out", "sprints": 10},
    7: {"label": "3+ PIs out", "sprints": 15},
}

# Entity type to simulation dimension mapping
ENTITY_DIMENSION_MAP = {
    "component": "architecture",
    "system": "architecture",
    "control": "compliance",
    "standard": "compliance",
    "requirement": "architecture",
    "vendor": "supply_chain",
    "supplier": "supply_chain",
    "document": "compliance",
}

ALL_DIMENSIONS = ["architecture", "compliance", "supply_chain", "schedule", "cost", "risk"]


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------


def _load_simulation_kg(project_id: str, db_path: Optional[str] = None) -> Tuple[Dict, Dict]:
    """Load or build a simulation Knowledge Graph from project data.

    Returns (nodes, adjacency) where:
      - nodes: {node_id: {"label", "entity_type", "dimension", "properties"}}
      - adjacency: {source_id: [(target_id, relationship, weight)]}
    """
    conn = get_connection(db_path)
    nodes: Dict[str, Dict] = {}
    adjacency: Dict[str, List] = {}

    # 1. Load SysML elements as architecture nodes
    try:
        rows = conn.execute(
            "SELECT id, name, stereotype, element_type FROM sysml_elements WHERE project_id = %s",
            (project_id,),
        ).fetchall()
        for r in rows:
            nid = f"sysml:{r['id']}"
            nodes[nid] = {
                "label": r["name"] or r["id"],
                "entity_type": "component",
                "dimension": "architecture",
                "properties": {"stereotype": r["stereotype"], "element_type": r["element_type"]},
            }
    except Exception:
        pass

    # 2. Load SysML relationships as edges
    try:
        rows = conn.execute(
            "SELECT source_id, target_id, relationship_type FROM sysml_relationships WHERE project_id = %s",
            (project_id,),
        ).fetchall()
        for r in rows:
            src = f"sysml:{r['source_id']}"
            tgt = f"sysml:{r['target_id']}"
            rel = r["relationship_type"] or "DEPENDS_ON"
            adjacency.setdefault(src, []).append((tgt, rel, 0.8))
    except Exception:
        pass

    # 3. Load compliance controls as compliance nodes
    try:
        rows = conn.execute(
            "SELECT id, control_id, implementation_status FROM project_controls WHERE project_id = %s",
            (project_id,),
        ).fetchall()
        for r in rows:
            nid = f"ctrl:{r['id']}"
            nodes[nid] = {
                "label": r["control_id"] or r["id"],
                "entity_type": "control",
                "dimension": "compliance",
                "properties": {"status": r["implementation_status"]},
            }
    except Exception:
        pass

    # 4. Load supply chain vendors
    try:
        rows = conn.execute(
            "SELECT id, vendor_name, scrm_risk_tier FROM supply_chain_vendors WHERE project_id = %s",
            (project_id,),
        ).fetchall()
        for r in rows:
            nid = f"vendor:{r['id']}"
            nodes[nid] = {
                "label": r["vendor_name"] or r["id"],
                "entity_type": "vendor",
                "dimension": "supply_chain",
                "properties": {"risk_tier": r["scrm_risk_tier"]},
            }
    except Exception:
        pass

    # 5. Load supply chain dependencies as edges
    try:
        rows = conn.execute(
            "SELECT vendor_id, depends_on_vendor_id, dependency_type FROM supply_chain_dependencies WHERE project_id = %s",
            (project_id,),
        ).fetchall()
        for r in rows:
            src = f"vendor:{r['vendor_id']}"
            tgt = f"vendor:{r['depends_on_vendor_id']}"
            adjacency.setdefault(src, []).append((tgt, "REQUIRES_RESOURCE", 0.7))
    except Exception:
        pass

    # 6. Load existing KG if available (kg_nodes/kg_edges for this project)
    try:
        graph_row = conn.execute(
            "SELECT id FROM kg_graphs WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if graph_row:
            gid = graph_row["id"]
            kg_nodes = conn.execute(
                "SELECT id, label, entity_type, properties, centrality FROM kg_nodes WHERE graph_id = %s",
                (gid,),
            ).fetchall()
            for r in kg_nodes:
                nid = f"kg:{r['id']}"
                etype = r["entity_type"] or "component"
                nodes[nid] = {
                    "label": r["label"],
                    "entity_type": etype,
                    "dimension": ENTITY_DIMENSION_MAP.get(etype, "architecture"),
                    "properties": json.loads(r["properties"]) if r["properties"] else {},
                    "centrality": r["centrality"] or 0.0,
                }
            kg_edges = conn.execute(
                "SELECT source_id, target_id, relationship, weight FROM kg_edges WHERE graph_id = %s",
                (gid,),
            ).fetchall()
            for r in kg_edges:
                src = f"kg:{r['source_id']}"
                tgt = f"kg:{r['target_id']}"
                adjacency.setdefault(src, []).append((tgt, r["relationship"] or "DEPENDS_ON", r["weight"] or 0.5))
    except Exception:
        pass

    conn.close()
    return nodes, adjacency


# ---------------------------------------------------------------------------
# BFS cascade
# ---------------------------------------------------------------------------


def run_cascade(
    project_id: str,
    start_node_ids: Optional[List[str]] = None,
    trigger_text: Optional[str] = None,
    max_depth: int = 7,
    max_width: int = 10,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run BFS cascade through the simulation KG.

    Args:
        project_id: Project to analyze.
        start_node_ids: Explicit node IDs to start from.
        trigger_text: Natural language trigger — finds matching nodes by label.
        max_depth: Maximum cascade depth (default 7).
        max_width: Maximum nodes per level (default 10).
        db_path: Optional DB path override.

    Returns:
        {
            "project_id": str,
            "trigger": str,
            "levels": {depth: [node_entries]},
            "total_levels": int,
            "total_nodes_discovered": int,
            "dimension_impact": {dim: {"node_count": int, "nodes": [...]}},
            "modifications_suggested": dict,
            "mermaid": str,
        }
    """
    nodes, adjacency = _load_simulation_kg(project_id, db_path)

    if not nodes:
        return {
            "project_id": project_id,
            "trigger": trigger_text or "unknown",
            "levels": {},
            "total_levels": 0,
            "total_nodes_discovered": 0,
            "dimension_impact": {},
            "modifications_suggested": {},
            "mermaid": "",
            "warning": "No KG nodes found for project. Seed with digital_thread.py or text_network.py first.",
        }

    # Resolve start nodes
    if not start_node_ids and trigger_text:
        start_node_ids = _find_nodes_by_text(trigger_text, nodes)

    if not start_node_ids:
        return {
            "project_id": project_id,
            "trigger": trigger_text or "unknown",
            "error": f"No matching nodes found for trigger: {trigger_text}",
            "available_nodes": len(nodes),
        }

    # BFS
    visited: Set[str] = set(start_node_ids)
    queue: deque = deque((sid, 0) for sid in start_node_ids)
    levels: Dict[int, List[Dict]] = {}
    dimension_hits: Dict[str, List[Dict]] = {d: [] for d in ALL_DIMENSIONS}

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        neighbors = adjacency.get(current_id, [])
        neighbors.sort(key=lambda x: x[2], reverse=True)

        added = 0
        for target_id, relationship, weight in neighbors:
            if target_id in visited or added >= max_width:
                continue
            visited.add(target_id)

            target = nodes.get(target_id, {})
            if not target:
                continue

            level = depth + 1
            timing = LEVEL_TIMING.get(level, LEVEL_TIMING[7])
            rel_info = CASCADE_RELATIONSHIPS.get(relationship, {})
            affected_dims = rel_info.get("dimensions", ["architecture"])

            node_entry = {
                "id": target_id,
                "label": target.get("label", "?"),
                "entity_type": target.get("entity_type", "unknown"),
                "dimension": target.get("dimension", "architecture"),
                "relationship": relationship,
                "weight": round(weight, 2),
                "level": level,
                "timing": timing["label"],
                "timing_sprints": timing["sprints"],
                "affected_dimensions": affected_dims,
                "source": nodes.get(current_id, {}).get("label", "?"),
                "properties": target.get("properties", {}),
            }

            lvl_list = levels.setdefault(level, [])
            lvl_list.append(node_entry)

            # Track dimension impacts
            for dim in affected_dims:
                dimension_hits[dim].append(node_entry)

            queue.append((target_id, level))
            added += 1

    # Build dimension impact summary
    dimension_impact = {}
    for dim, hits in dimension_hits.items():
        if hits:
            dimension_impact[dim] = {
                "node_count": len(hits),
                "nodes": [{"label": h["label"], "level": h["level"], "relationship": h["relationship"]} for h in hits],
                "max_depth": max(h["level"] for h in hits),
                "earliest_sprint": min(h["timing_sprints"] for h in hits),
            }

    # Auto-suggest modifications based on cascade discoveries
    mods = _suggest_modifications(dimension_impact, levels)

    # Build Mermaid diagram
    mermaid = _build_mermaid(trigger_text or "Trigger", start_node_ids, levels, nodes)

    return {
        "project_id": project_id,
        "trigger": trigger_text or ", ".join(start_node_ids),
        "levels": {str(k): v for k, v in sorted(levels.items())},
        "total_levels": len(levels),
        "total_nodes_discovered": sum(len(v) for v in levels.values()),
        "dimension_impact": dimension_impact,
        "modifications_suggested": mods,
        "mermaid": mermaid,
    }


def _find_nodes_by_text(text: str, nodes: Dict) -> List[str]:
    """Find node IDs whose label matches the trigger text (fuzzy)."""
    text_lower = text.lower()
    keywords = text_lower.split()
    matches = []

    for nid, node in nodes.items():
        label = node.get("label", "").lower()
        # Exact substring match
        if text_lower in label:
            matches.append((nid, 1.0))
            continue
        # Keyword overlap
        overlap = sum(1 for kw in keywords if kw in label)
        if overlap > 0:
            matches.append((nid, overlap / len(keywords)))

    matches.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in matches[:5]]  # Top 5 matches


def _suggest_modifications(dimension_impact: Dict[str, Dict], levels: Dict[int, List]) -> Dict[str, Any]:
    """Auto-generate modification suggestions based on cascade discoveries."""
    mods: Dict[str, Any] = {}
    total_nodes = sum(d.get("node_count", 0) for d in dimension_impact.values())

    if "architecture" in dimension_impact:
        arch = dimension_impact["architecture"]
        mods["add_requirements"] = max(1, arch["node_count"] // 2)
        if arch["node_count"] > 5:
            mods["change_architecture"] = {"add_components": arch["node_count"] // 3}

    if "compliance" in dimension_impact:
        comp = dimension_impact["compliance"]
        if comp["node_count"] > 3:
            mods["compliance_controls_affected"] = comp["node_count"]

    if "supply_chain" in dimension_impact:
        sc = dimension_impact["supply_chain"]
        mods["supply_chain_vendors_affected"] = sc["node_count"]

    if "schedule" in dimension_impact:
        sched = dimension_impact["schedule"]
        max_sprint = sched.get("max_depth", 0)
        if max_sprint > 3:
            mods["schedule_extension_weeks"] = max_sprint * 2

    if "risk" in dimension_impact:
        risk = dimension_impact["risk"]
        if risk["node_count"] > 3:
            mods["risk_escalation"] = True

    if total_nodes > 10:
        mods["cascade_severity"] = "HIGH"
    elif total_nodes > 5:
        mods["cascade_severity"] = "MEDIUM"
    else:
        mods["cascade_severity"] = "LOW"

    return mods


def _build_mermaid(
    trigger_label: str,
    start_ids: List[str],
    levels: Dict[int, List],
    nodes: Dict,
) -> str:
    """Build a Mermaid flowchart from cascade levels."""
    lines = ["graph TD"]

    def safe(s):
        return re.sub(r"[^a-zA-Z0-9_]", "_", str(s))[:30]

    # Import re at function scope
    import re

    # Trigger node
    trigger_id = safe(trigger_label)
    lines.append(f'    {trigger_id}["{trigger_label}"]')
    lines.append(f"    style {trigger_id} fill:#f44336,color:#fff")

    # Color map by dimension
    dim_colors = {
        "architecture": "#2196f3",
        "compliance": "#4caf50",
        "supply_chain": "#ff9800",
        "schedule": "#9c27b0",
        "cost": "#795548",
        "risk": "#f44336",
    }

    seen_ids = set()
    for level_num, entries in sorted(levels.items()):
        for entry in entries:
            nid = safe(entry.get("id", entry["label"]))
            if nid in seen_ids:
                continue
            seen_ids.add(nid)

            label = entry["label"][:25]
            dim = entry.get("dimension", "architecture")
            color = dim_colors.get(dim, "#607d8b")
            rel = entry.get("relationship", "DEPENDS_ON")
            source_label = entry.get("source", trigger_label)
            source_id = (
                safe(source_label) if safe(source_label) in seen_ids or safe(source_label) == trigger_id else trigger_id
            )

            lines.append(f'    {source_id} -->|{rel}| {nid}["{label}"]')
            lines.append(f"    style {nid} fill:{color},color:#fff")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Cascade-to-Simulation Bridge")
    parser.add_argument("--project-id", dest="project_id", help="Project ID")
    parser.add_argument("--project", dest="project_id", help=argparse.SUPPRESS)  # backward compat — use --project-id
    parser.add_argument("--trigger", help="Natural language trigger text")
    parser.add_argument("--node", help="Specific node ID to start from")
    parser.add_argument("--depth", type=int, default=7, help="Max cascade depth")
    parser.add_argument("--width", type=int, default=10, help="Max nodes per level")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Gate mode — exit 1 if HIGH severity")
    args = parser.parse_args()

    if not args.project_id:
        parser.error("--project-id is required")
    start_ids = [args.node] if args.node else None
    result = run_cascade(
        project_id=args.project_id,
        start_node_ids=start_ids,
        trigger_text=args.trigger,
        max_depth=args.depth,
        max_width=args.width,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Trigger:    {result.get('trigger', '?')}")
        print(f"Levels:     {result.get('total_levels', 0)}")
        print(f"Nodes:      {result.get('total_nodes_discovered', 0)}")
        dim_impact = result.get("dimension_impact", {})
        if dim_impact:
            print("Dimension impacts:")
            for dim, info in dim_impact.items():
                print(f"  {dim}: {info['node_count']} nodes, depth {info.get('max_depth', 0)}")
        mods = result.get("modifications_suggested", {})
        if mods:
            print(f"Suggested:  {json.dumps(mods, indent=2)}")
        severity = mods.get("cascade_severity", "LOW")
        print(f"Severity:   {severity}")

    if args.gate and result.get("modifications_suggested", {}).get("cascade_severity") == "HIGH":
        sys.exit(1)


if __name__ == "__main__":
    main()
