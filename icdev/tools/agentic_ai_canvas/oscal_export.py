# CUI // SP-CTI
"""Agentic AI Design Canvas — OSCAL Component Definition export (Phase 5).

Generates an OSCAL 1.1 Component Definition JSON from an AADC design graph.
Maps AADC nodes to NIST 800-53 control implementations for FedRAMP/ATO use.

Output conforms to NIST SP 800-53 Component Definition schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


# Node type → list of NIST 800-53 controls implemented
_NODE_CONTROL_MAP: dict[str, list[str]] = {
    "hitl-gate":            ["AC-3", "AC-6", "CM-9", "SI-12"],
    "approval-workflow":    ["AC-3", "AC-6", "SA-4"],
    "audit-logger":         ["AU-2", "AU-3", "AU-9", "AU-12"],
    "guardrail":            ["SI-3", "SI-10", "SC-7"],
    "pii-detector":         ["SC-28", "SI-12", "PL-8"],
    "pii-field-detector":   ["SC-28", "SI-12", "PL-8"],
    "redaction-engine":     ["SC-28", "SI-12"],
    "toxicity-filter":      ["SI-3", "SI-10"],
    "input-sanitizer":      ["SI-10", "SI-3"],
    "circuit-breaker":      ["SI-17", "SA-17", "SC-5"],
    "confidence-threshold": ["SI-17", "SA-17"],
    "rate-limiter":         ["SC-5", "SA-17"],
    "trusted-monitor":      ["SI-4", "AU-6"],
    "drift-detector":       ["SI-4", "CA-7"],
    "baseline-snapshot":    ["CA-7", "SI-4"],
    "compliance-reporter":  ["CA-2", "CA-7", "AU-6"],
    "llm":                  ["SA-11", "SA-15", "CM-7"],
    "llm-local":            ["SA-11", "SA-15", "CM-7", "SC-28"],
    "mcp-server":           ["CM-7", "SA-9", "SC-7"],
    "mcp-gateway":          ["SC-7", "AC-3", "SA-9"],
    "a2a-bridge":           ["SC-8", "SC-7", "AC-3"],
    "sandbox-exec":         ["CM-7", "SC-7", "SI-3"],
    "prompt-registry":      ["CM-3", "CM-7", "SA-15"],
    "caio-override":        ["AC-2", "AC-6", "CM-9"],
    "vector-db":            ["SC-28", "AU-9"],
    "knowledge-graph":      ["SC-28", "AU-9"],
}


def export_oscal_component(design: dict, graph: dict, assessment: dict | None) -> dict:
    """
    Generate OSCAL 1.1 Component Definition JSON.

    Args:
        design: design metadata dict
        graph: {nodes, edges}
        assessment: latest assessment dict (optional)

    Returns:
        OSCAL Component Definition as a Python dict (serialize to JSON for export)
    """
    now = datetime.now(timezone.utc).isoformat()
    design_id = design.get("id", "unknown")
    design_name = design.get("name", "Unnamed Design")
    classification = design.get("classification", "CUI")
    nodes = graph.get("nodes", [])

    # Collect all controls from nodes present in the design
    controls_present: dict[str, list[str]] = {}  # control_id → list of node labels
    for node in nodes:
        ntype = node.get("type", "")
        for ctrl in _NODE_CONTROL_MAP.get(ntype, []):
            controls_present.setdefault(ctrl, []).append(node.get("label", node["id"]))

    # Build implemented requirements
    implemented_requirements = []
    for ctrl, implementing_nodes in sorted(controls_present.items()):
        implemented_requirements.append({
            "uuid": str(uuid.uuid4()),
            "control-id": ctrl.lower(),
            "description": f"Implemented by: {', '.join(implementing_nodes[:3])}{'...' if len(implementing_nodes) > 3 else ''}",
            "remarks": f"AADC node type(s) provide this control. Design: {design_name}.",
            "props": [
                {"name": "implementation-status", "value": "implemented"},
                {"name": "aadc-design-id", "value": design_id},
            ],
        })

    # Score remarks
    score_remarks = ""
    if assessment:
        score_remarks = (
            f"NIST AI RMF: {assessment.get('nist_rmf_score', 0):.1f}%, "
            f"OWASP LLM: {assessment.get('owasp_score', 0):.1f}%, "
            f"Overall: {assessment.get('score', 0):.1f}%"
        )

    component = {
        "uuid": str(uuid.uuid4()),
        "type": "software",
        "title": design_name,
        "description": f"Agentic AI system design exported from ICDEV AADC. Classification: {classification}.",
        "props": [
            {"name": "version", "value": "1.0"},
            {"name": "release-date", "value": now[:10]},
            {"name": "classification", "value": classification},
            {"name": "autonomy-level", "value": str(assessment.get("autonomy_max", 0) if assessment else 0)},
            {"name": "aadc-score", "value": score_remarks},
        ],
        "control-implementations": [
            {
                "uuid": str(uuid.uuid4()),
                "source": "https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final",
                "description": f"NIST SP 800-53 Rev 5 controls implemented by AADC design '{design_name}'.",
                "implemented-requirements": implemented_requirements,
            }
        ],
    }

    return {
        "component-definition": {
            "uuid": str(uuid.uuid4()),
            "metadata": {
                "title": f"OSCAL Component Definition — {design_name}",
                "last-modified": now,
                "version": "1.0",
                "oscal-version": "1.1.0",
                "remarks": f"Auto-generated by ICDEV AADC on {now[:10]}. {score_remarks}",
                "props": [{"name": "classification", "value": classification}],
            },
            "components": [component],
        }
    }


def get_control_coverage_summary(nodes: list[dict]) -> dict:
    """Return a summary of which NIST 800-53 control families are covered."""
    families: dict[str, int] = {}
    covered: set[str] = set()
    for node in nodes:
        for ctrl in _NODE_CONTROL_MAP.get(node.get("type", ""), []):
            family = ctrl.split("-")[0]
            covered.add(ctrl)
            families[family] = families.get(family, 0) + 1
    return {"controls_covered": len(covered), "families": families, "control_ids": sorted(covered)}
