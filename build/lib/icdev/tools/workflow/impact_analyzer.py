#!/usr/bin/env python3
# CUI // SP-CTI
"""Integration Impact Analyzer — cross-subsystem gap detection (D-WF-8f).

Given a list of changed files or DB tables, walks the integration matrix
(args/integration_matrix.yaml) to identify subsystems that may need
corresponding updates. LLM-agnostic — any AI coding assistant can call this.

Usage:
    python tools/workflow/impact_analyzer.py --analyze --changed-files "tools/workflow/loop_engine.py" --json
    python tools/workflow/impact_analyzer.py --analyze --changed-tables "workflow_loops,workflow_handoffs" --json
    python tools/workflow/impact_analyzer.py --graph --json
    python tools/workflow/impact_analyzer.py --analyze --changed-files "tools/workflow/loop_engine.py" --human
"""

from __future__ import annotations

import argparse
import json
from tools.common.helpers import now_iso
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX_PATH = PROJECT_ROOT / "args" / "integration_matrix.yaml"

_HAS_YAML = False
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    pass


def _load_matrix() -> Dict[str, Any]:
    if not _HAS_YAML or not MATRIX_PATH.exists():
        return {}
    try:
        with open(MATRIX_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("subsystems", {})
    except Exception:
        return {}


def _identify_subsystem_from_file(file_path: str, matrix: Dict[str, Any]) -> Set[str]:
    """Identify which subsystem(s) a file belongs to based on path heuristics."""
    fp = file_path.lower().replace("\\", "/")
    matched: Set[str] = set()

    # Direct path mapping
    path_map = {
        "tools/workflow/": "workflow",
        "tools/dashboard/chat": "chat",
        "tools/requirements/intake": "intake",
        "tools/requirements/decomposition": "decomposition",
        "tools/requirements/readiness": "intake",
        "tools/requirements/gap": "intake",
        "tools/compliance/": "compliance",
        "tools/security/": "security",
        "tools/genesis/": "genesis",
        "tools/registry/": "evolution",
        "tools/knowledge/": "knowledge",
        "tools/innovation/": "innovation",
        "tools/rag/": "rag",
        "tools/finetune/": "finetune",
        "tools/intelligence/bayesian": "bayesian_teaching",
        "tools/extensions/builtins/": "chat",  # Extensions hook into chat
        "args/ai_governance": "governance",
        "tools/compliance/ai_": "governance",
        "tools/compliance/accountability": "governance",
    }

    for prefix, subsystem in path_map.items():
        if prefix in fp:
            matched.add(subsystem)

    return matched


def _identify_subsystem_from_table(table_name: str, matrix: Dict[str, Any]) -> Set[str]:
    """Identify which subsystem owns a table."""
    tbl = table_name.lower()
    matched: Set[str] = set()

    for subsystem_id, info in matrix.items():
        tables = [t.lower() for t in info.get("tables", [])]
        if tbl in tables:
            matched.add(subsystem_id)

    return matched


def analyze_impact(
    changed_files: Optional[List[str]] = None,
    changed_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Analyze integration impact of changes.

    Walks the matrix graph to find connected subsystems that may need updates.
    Returns structured impact report with recommendations.
    """
    matrix = _load_matrix()
    if not matrix:
        return {
            "error": "Integration matrix not found or empty",
            "hint": "Ensure args/integration_matrix.yaml exists and pyyaml is installed",
        }

    # Identify affected subsystems
    affected: Set[str] = set()

    if changed_files:
        for fp in changed_files:
            affected |= _identify_subsystem_from_file(fp, matrix)

    if changed_tables:
        for tbl in changed_tables:
            affected |= _identify_subsystem_from_table(tbl, matrix)

    if not affected:
        return {
            "affected_subsystems": [],
            "impacted_subsystems": [],
            "recommendations": [],
            "message": "No subsystems identified from the changes",
            "analyzed_at": now_iso(),
        }

    # Walk graph to find connected subsystems
    impacted: Dict[str, Dict[str, Any]] = {}
    for subsystem_id in affected:
        info = matrix.get(subsystem_id, {})
        connects_to = info.get("connects_to", [])
        for connected in connects_to:
            if connected not in affected:
                # This connected subsystem wasn't changed — potential gap
                connected_info = matrix.get(connected, {})
                if connected not in impacted:
                    impacted[connected] = {
                        "subsystem": connected,
                        "triggered_by": [],
                        "integration_type": connected_info.get("integration_type", "unknown"),
                        "tables": connected_info.get("tables", []),
                    }
                impacted[connected]["triggered_by"].append(subsystem_id)

    # Generate recommendations
    recommendations: List[Dict[str, str]] = []
    for sub_id, impact in impacted.items():
        triggers = ", ".join(impact["triggered_by"])
        int_type = impact["integration_type"]

        if int_type == "extension_hook":
            recommendations.append(
                {
                    "subsystem": sub_id,
                    "action": f"Check if {sub_id} extension hooks need updating for changes in {triggers}",
                    "severity": "medium",
                    "type": "extension_hook",
                }
            )
        elif int_type == "bridge_function":
            recommendations.append(
                {
                    "subsystem": sub_id,
                    "action": f"Verify {sub_id} bridge functions reflect changes in {triggers}",
                    "severity": "medium",
                    "type": "bridge_function",
                }
            )
        elif int_type == "db_query":
            recommendations.append(
                {
                    "subsystem": sub_id,
                    "action": f"Check if {sub_id} DB queries need updating for schema changes in {triggers}",
                    "severity": "high" if "schema" in str(impact.get("tables", [])) else "medium",
                    "type": "db_query",
                }
            )
        else:
            recommendations.append(
                {
                    "subsystem": sub_id,
                    "action": f"Review {sub_id} for potential integration with {triggers}",
                    "severity": "low",
                    "type": "review",
                }
            )

    return {
        "affected_subsystems": sorted(affected),
        "impacted_subsystems": sorted(impacted.keys()),
        "impact_details": impacted,
        "recommendations": recommendations,
        "total_affected": len(affected),
        "total_impacted": len(impacted),
        "total_recommendations": len(recommendations),
        "analyzed_at": now_iso(),
    }


def get_graph() -> Dict[str, Any]:
    """Return the full integration matrix as a graph."""
    matrix = _load_matrix()
    if not matrix:
        return {"error": "Integration matrix not found"}

    nodes = []
    edges = []
    for sub_id, info in matrix.items():
        nodes.append(
            {
                "id": sub_id,
                "tables": info.get("tables", []),
                "integration_type": info.get("integration_type", ""),
            }
        )
        for connected in info.get("connects_to", []):
            edges.append({"from": sub_id, "to": connected})

    return {
        "nodes": nodes,
        "edges": edges,
        "total_subsystems": len(nodes),
        "total_edges": len(edges),
    }


def _format_human(result: Dict[str, Any]) -> str:
    lines = [
        "",
        "=" * 60,
        "  INTEGRATION IMPACT ANALYSIS",
        "=" * 60,
        "",
    ]

    affected = result.get("affected_subsystems", [])
    if affected:
        lines.append(f"  Changed subsystems: {', '.join(affected)}")
    else:
        lines.append("  No subsystems identified from changes")

    impacted = result.get("impacted_subsystems", [])
    if impacted:
        lines.append(f"  Potentially impacted: {', '.join(impacted)}")
        lines.append("")

    for rec in result.get("recommendations", []):
        icon = {"high": "!", "medium": "*", "low": "-"}.get(rec["severity"], "-")
        lines.append(f"  [{icon}] {rec['action']}")

    lines.extend(["", f"  Total recommendations: {result.get('total_recommendations', 0)}", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Integration Impact Analyzer — cross-subsystem gap detection")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--human", action="store_true")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--analyze", action="store_true", help="Analyze impact of changes")
    group.add_argument("--graph", action="store_true", help="Show full integration graph")

    parser.add_argument("--changed-files", type=str, default="", help="Comma-separated changed file paths")
    parser.add_argument("--changed-tables", type=str, default="", help="Comma-separated changed table names")

    args = parser.parse_args()

    if args.analyze:
        files = [f.strip() for f in args.changed_files.split(",") if f.strip()] if args.changed_files else None
        tables = [t.strip() for t in args.changed_tables.split(",") if t.strip()] if args.changed_tables else None
        result = analyze_impact(files, tables)
    else:
        result = get_graph()

    if args.human:
        print(_format_human(result))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
