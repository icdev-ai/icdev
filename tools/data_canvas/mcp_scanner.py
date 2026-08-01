#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Data Design Canvas — MCP (Model Context Protocol) Scanner.

Scans data design graphs for MCP server node definitions and validates:
  - Tool naming conventions (snake_case, max 64 chars)
  - Resource URI format (protocol://authority/path)
  - Prompt template completeness (name, description, arguments)
  - Duplicate tool/resource registration across nodes
  - Missing required MCP fields

Output: {findings, overall_risk, node_count, mcp_nodes, scanned_at}

Usage:
  python -m tools.data_canvas.mcp_scanner --graph-json design.json --output-json
  python -m tools.data_canvas.mcp_scanner --design-id <uuid> --output-json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}[a-z0-9]$|^[a-z]$")
_URI_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://[^/\s]+")
_RISK_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}

_MCP_NODE_TYPES = {"mcp-server", "mcp_server", "tool-mcp", "mcp"}


def _is_mcp_node(node: dict) -> bool:
    ntype = str(node.get("type", "") or node.get("node_type", "")).lower()
    return any(t in ntype for t in _MCP_NODE_TYPES)


def _overall_risk(findings: list[dict]) -> str:
    if not findings:
        return "none"
    max_level = max((_RISK_ORDER.get(f.get("severity", "none"), 0) for f in findings), default=0)
    return {3: "high", 2: "medium", 1: "low", 0: "none"}[max_level]


def _check_tool(tool: dict, node_id: str, seen_tools: set[str]) -> list[dict]:
    findings = []
    name = str(tool.get("name") or "")

    if not name:
        findings.append({
            "node_id": node_id, "severity": "high", "finding_type": "missing_tool_name",
            "description": "MCP tool definition is missing a 'name' field.",
        })
        return findings

    if name in seen_tools:
        findings.append({
            "node_id": node_id, "severity": "medium", "finding_type": "duplicate_tool",
            "description": f"Tool '{name}' is registered more than once across MCP nodes.",
        })
    seen_tools.add(name)

    if not _SNAKE_CASE_RE.match(name):
        findings.append({
            "node_id": node_id, "severity": "medium", "finding_type": "invalid_tool_name",
            "description": (
                f"Tool name '{name}' should be snake_case, start with a letter, "
                "and be ≤64 characters."
            ),
        })

    if not tool.get("description"):
        findings.append({
            "node_id": node_id, "severity": "low", "finding_type": "missing_tool_description",
            "description": f"Tool '{name}' is missing a 'description'.",
        })

    return findings


def _check_resource(resource: dict, node_id: str, seen_uris: set[str]) -> list[dict]:
    findings = []
    uri = str(resource.get("uri") or "")

    if not uri:
        findings.append({
            "node_id": node_id, "severity": "high", "finding_type": "missing_resource_uri",
            "description": "MCP resource is missing a 'uri' field.",
        })
        return findings

    if uri in seen_uris:
        findings.append({
            "node_id": node_id, "severity": "medium", "finding_type": "duplicate_resource_uri",
            "description": f"Resource URI '{uri}' is registered more than once.",
        })
    seen_uris.add(uri)

    if not _URI_RE.match(uri):
        findings.append({
            "node_id": node_id, "severity": "medium", "finding_type": "invalid_resource_uri",
            "description": f"Resource URI '{uri}' does not match protocol://authority/path format.",
        })

    if not resource.get("name"):
        findings.append({
            "node_id": node_id, "severity": "low", "finding_type": "missing_resource_name",
            "description": f"Resource '{uri}' is missing a human-readable 'name'.",
        })

    return findings


def _check_prompt(prompt: dict, node_id: str) -> list[dict]:
    findings = []
    name = str(prompt.get("name") or "")
    if not name:
        findings.append({
            "node_id": node_id, "severity": "high", "finding_type": "missing_prompt_name",
            "description": "MCP prompt template is missing a 'name' field.",
        })
    if not prompt.get("description"):
        findings.append({
            "node_id": node_id, "severity": "low", "finding_type": "missing_prompt_description",
            "description": f"Prompt '{name or '?'}' is missing a 'description'.",
        })
    return findings


def scan_graph(graph: dict) -> dict[str, Any]:
    """Scan a design graph dict for MCP node compliance issues.

    Args:
        graph: Design graph with 'nodes' list (each node has 'id', 'type', 'properties').

    Returns:
        {findings, overall_risk, node_count, mcp_nodes, scanned_at}
    """
    nodes = graph.get("nodes", [])
    findings: list[dict] = []
    mcp_node_ids: list[str] = []
    seen_tools: set[str] = set()
    seen_uris: set[str] = set()

    for node in nodes:
        if not _is_mcp_node(node):
            continue
        node_id = str(node.get("id", "?"))
        mcp_node_ids.append(node_id)
        props = node.get("properties") or {}

        # Check tools
        for tool in (props.get("tools") or []):
            findings.extend(_check_tool(tool, node_id, seen_tools))

        # Check resources
        for resource in (props.get("resources") or []):
            findings.extend(_check_resource(resource, node_id, seen_uris))

        # Check prompts
        for prompt in (props.get("prompts") or []):
            findings.extend(_check_prompt(prompt, node_id))

        # Must declare at least one tool or resource
        if not props.get("tools") and not props.get("resources"):
            findings.append({
                "node_id": node_id, "severity": "medium", "finding_type": "empty_mcp_node",
                "description": "MCP server node has no tools or resources declared.",
            })

    return {
        "findings": findings,
        "overall_risk": _overall_risk(findings),
        "node_count": len(nodes),
        "mcp_nodes": len(mcp_node_ids),
        "mcp_node_ids": mcp_node_ids,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def scan_design_id(design_id: str) -> dict[str, Any]:
    """Load a design by ID from the DDC DB and scan its graph."""
    # data_designs is a canvas table without classification/tenant_id, so use
    # get_canvas_connection() to avoid the global RLS predicate (UndefinedColumn).
    from tools.db.storage import get_canvas_connection
    conn = get_canvas_connection()

    try:
        row = conn.execute(
            "SELECT graph_json FROM data_designs WHERE id = ?", (design_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"error": f"Design '{design_id}' not found.", "findings": [], "overall_risk": "none"}

    graph_json = row[0] if isinstance(row, (list, tuple)) else row.get("graph_json", "{}")
    try:
        graph = json.loads(graph_json or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"Invalid graph JSON: {exc}", "findings": [], "overall_risk": "none"}

    result = scan_graph(graph)
    result["design_id"] = design_id
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="DDC MCP Scanner — validate MCP node definitions")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--graph-json", metavar="FILE", help="Design graph JSON file")
    src.add_argument("--design-id", metavar="UUID", help="Design ID to load from DDC DB")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    if args.graph_json:
        try:
            with open(args.graph_json, encoding="utf-8") as fh:
                graph = json.load(fh)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        result = scan_graph(graph)
    else:
        result = scan_design_id(args.design_id)

    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[mcp_scanner] {result['mcp_nodes']} MCP nodes / {result['node_count']} total — "
              f"risk={result['overall_risk']} — {len(result['findings'])} finding(s)")
        for f in result["findings"]:
            print(f"  [{f['severity'].upper():6s}] {f['node_id']}: {f['description']}")


if __name__ == "__main__":
    main()
