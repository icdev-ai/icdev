# [CUI // SP-CTI]
"""ICDEV™ Network Canvas — Topology Validator/Auditor.

Scans a topology for issues and auto-fixes them:
  - Unknown device types (shows '?' on canvas) → reclassify
  - Missing site assignments
  - Orphan edges (referencing removed nodes)
  - Duplicate node IDs

Run after ingestion/enrichment to ensure diagram quality.

Usage::

    python tools/network/topology_validator.py --topology-id T1 --fix --json
    python tools/network/topology_validator.py --all --fix --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.network.topology_validator")


def validate_topology(topology_id: str, fix: bool = False) -> dict[str, Any]:
    """Validate and optionally fix a topology.

    Returns dict with issues found and fixes applied.
    """

    from tools.network.network_ingester import _classify_device_type

    conn = get_connection(str(BASE_DIR / "data" / "network_canvas.db"))

    row = conn.execute("SELECT graph_json, name FROM topologies WHERE id = %s", (topology_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"Topology {topology_id} not found"}

    topo_name = row["name"]

    # ── Guard: persisted graph_json must be valid JSON encoding an object ──
    # Malformed persisted data is reported as a fail-closed finding, never
    # allowed to crash the validator.
    try:
        graph = json.loads(row["graph_json"])
        if not isinstance(graph, dict):
            raise ValueError("graph_json did not decode to a JSON object")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        conn.close()
        return {
            "topology_id": topology_id,
            "topology_name": topo_name,
            "total_nodes": 0,
            "total_edges": 0,
            "issues_found": 1,
            "unknown_types": 0,
            "orphan_edges": 0,
            "fixes_applied": 0,
            "issues": [{
                "type": "invalid_graph_json",
                "severity": "error",
                "message": "graph_json is not valid JSON",
                "detail": str(exc),
            }],
            "fixes": [],
            "passed": False,
        }

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    issues: list[dict] = []
    fixes: list[dict] = []
    node_ids = set()

    # ── Check 0: Malformed nodes (non-dict / missing id) ──────────────
    # A node that is not a dict, or a dict without an "id", would crash the
    # downstream checks. Report each as a finding and exclude it from the
    # remaining checks so the rest of the topology is still validated.
    valid_nodes: list[dict] = []
    for idx, n in enumerate(nodes):
        if not isinstance(n, dict):
            issues.append({
                "type": "malformed_node",
                "severity": "error",
                "node_index": idx,
                "content": repr(n)[:120],
                "message": f"Node at index {idx} is not an object: {repr(n)[:120]}",
            })
            continue
        if "id" not in n:
            issues.append({
                "type": "malformed_node",
                "severity": "error",
                "node_index": idx,
                "content": json.dumps(n)[:120],
                "message": f"Node at index {idx} is missing 'id': {json.dumps(n)[:120]}",
            })
            continue
        valid_nodes.append(n)

    # ── Check 1: Unknown device types ─────────────────────────────────
    for n in valid_nodes:
        ntype = n.get("type", "")
        if ntype == "group-site":
            continue

        node_ids.add(n["id"])
        label = n.get("label", "")

        if ntype == "unknown" or ntype == "":
            # Try reclassifying
            new_type = _classify_device_type(label, ntype)
            if new_type != "unknown":
                issues.append({
                    "type": "unknown_device_type",
                    "severity": "warning",
                    "node_id": n["id"],
                    "label": label,
                    "old_type": ntype,
                    "new_type": new_type,
                    "message": f"'{label}' was 'unknown', reclassified to '{new_type}'",
                })
                if fix:
                    n["type"] = new_type
                    fixes.append({"action": "reclassify", "node_id": n["id"], "label": label, "from": ntype, "to": new_type})
            else:
                issues.append({
                    "type": "unknown_device_type",
                    "severity": "error",
                    "node_id": n["id"],
                    "label": label,
                    "message": f"'{label}' is 'unknown' and could not be auto-classified. Will show '?' on canvas.",
                })

    # ── Check 2: Orphan edges ─────────────────────────────────────────
    orphan_edges = []
    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src not in node_ids or tgt not in node_ids:
            orphan_edges.append(e)
            issues.append({
                "type": "orphan_edge",
                "severity": "warning",
                "edge_id": e.get("id", ""),
                "source": src,
                "target": tgt,
                "message": "Edge references missing node(s)",
            })

    if fix and orphan_edges:
        graph["edges"] = [e for e in edges if e not in orphan_edges]
        fixes.append({"action": "remove_orphan_edges", "count": len(orphan_edges)})

    # ── Check 3: Duplicate node IDs ───────────────────────────────────
    seen_ids: dict[str, int] = {}
    for n in valid_nodes:
        nid = n["id"]
        seen_ids[nid] = seen_ids.get(nid, 0) + 1
    dupes = {k: v for k, v in seen_ids.items() if v > 1}
    for nid, count in dupes.items():
        issues.append({
            "type": "duplicate_node_id",
            "severity": "error",
            "node_id": nid,
            "count": count,
            "message": f"Node ID '{nid}' appears {count} times",
        })

    # ── Save fixes ────────────────────────────────────────────────────
    if fix and fixes:
        conn.execute("UPDATE topologies SET graph_json = %s WHERE id = %s", (json.dumps(graph), topology_id))
        conn.commit()

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────
    unknown_count = sum(1 for i in issues if i["type"] == "unknown_device_type")
    orphan_count = sum(1 for i in issues if i["type"] == "orphan_edge")

    return {
        "topology_id": topology_id,
        "topology_name": topo_name,
        "total_nodes": len([n for n in valid_nodes if n.get("type") != "group-site"]),
        "total_edges": len(edges),
        "issues_found": len(issues),
        "unknown_types": unknown_count,
        "orphan_edges": orphan_count,
        "fixes_applied": len(fixes),
        "issues": issues,
        "fixes": fixes,
        "passed": len(issues) == 0 or (fix and unknown_count == sum(1 for f in fixes if f["action"] == "reclassify")),
    }


def validate_all(fix: bool = False) -> dict[str, Any]:
    """Validate all topologies."""

    conn = get_connection(str(BASE_DIR / "data" / "network_canvas.db"))
    rows = conn.execute("SELECT id FROM topologies ORDER BY created_at DESC").fetchall()
    conn.close()

    results = []
    total_issues = 0
    total_fixes = 0

    for r in rows:
        tid = r[0] if isinstance(r, tuple) else r["id"]
        result = validate_topology(tid, fix=fix)
        results.append(result)
        total_issues += result.get("issues_found", 0)
        total_fixes += result.get("fixes_applied", 0)

    return {
        "topologies_checked": len(results),
        "total_issues": total_issues,
        "total_fixes": total_fixes,
        "all_passed": all(r.get("passed") or r.get("issues_found", 0) == 0 for r in results),
        "results": results,
    }


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Topology Validator")
    parser.add_argument("--topology-id", help="Validate a specific topology")
    parser.add_argument("--all", action="store_true", help="Validate all topologies")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if issues found")
    args = parser.parse_args()

    if args.all:
        result = validate_all(fix=args.fix)
    elif args.topology_id:
        result = validate_topology(args.topology_id, fix=args.fix)
    else:
        parser.print_help()
        return

    if args.json:
        # Compact output for --gate
        if args.gate:
            print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2, default=str))
        else:
            print(json.dumps(result, indent=2, default=str))
    else:
        if "results" in result:
            for r in result["results"]:
                status = "PASS" if r.get("passed") or r.get("issues_found", 0) == 0 else "FAIL"
                print(f"  {status}: {r['topology_name']} — {r['issues_found']} issues, {r['fixes_applied']} fixed")
                for i in r.get("issues", []):
                    print(f"    [{i['severity']}] {i['message']}")
            print(f"\n  Total: {result['topologies_checked']} topologies, {result['total_issues']} issues, {result['total_fixes']} fixes")
        else:
            print(f"  {result.get('topology_name', '?')}: {result.get('issues_found', 0)} issues")
            for i in result.get("issues", []):
                print(f"    [{i['severity']}] {i['message']}")

    if args.gate and not result.get("all_passed", result.get("passed", True)):
        sys.exit(1)


if __name__ == "__main__":
    main()
