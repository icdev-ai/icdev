# CUI // SP-CTI
"""Migration Design Canvas — Assessment & Analysis Engine.

Pure deterministic functions: no LLM, no API keys.
Assesses migration designs against compliance rules, detects gaps,
computes readiness scores, and suggests wave optimization.
"""

from __future__ import annotations

import json
from typing import Any

from tools.migration_canvas.constants import MC_COMPLIANCE_RULES


# ── Helpers ─────────────────────────────────────────────────────────────────


def _parse_graph(graph_json: Any) -> dict:
    """Normalise graph_json to a dict with 'nodes' and 'edges' lists."""
    if isinstance(graph_json, str):
        try:
            graph_json = json.loads(graph_json)
        except (json.JSONDecodeError, TypeError):
            return {"nodes": [], "edges": []}
    if not isinstance(graph_json, dict):
        return {"nodes": [], "edges": []}
    return {
        "nodes": graph_json.get("nodes", []),
        "edges": graph_json.get("edges", []),
    }


def _node_prefix(node_type: str) -> str:
    """Return the category prefix of a node type (e.g. 'src' from 'src-legacy')."""
    return (node_type or "").split("-")[0]


def _nodes_by_prefix(nodes: list, prefix: str) -> list:
    return [n for n in nodes if _node_prefix(n.get("type", "")) == prefix]


def _edge_targets(edges: list, source_id: str) -> list:
    return [e["target"] for e in edges if e.get("source") == source_id]


def _edge_sources(edges: list, target_id: str) -> list:
    return [e["source"] for e in edges if e.get("target") == target_id]


def _node_by_id(nodes: list, node_id: str) -> dict | None:
    for n in nodes:
        if n.get("id") == node_id:
            return n
    return None


# ── Assessment ──────────────────────────────────────────────────────────────


def assess_migration_design(graph_json: Any) -> dict:
    """Assess a migration design against compliance rules.

    Returns:
        {
            "findings": [{"rule_id", "title", "severity", "category", "description", "detail"}],
            "score": 0-100,
            "grade": "A"|"B"|"C"|"D"|"F",
            "cat1_count": int,
            "cat2_count": int,
            "cat3_count": int,
        }
    """
    graph = _parse_graph(graph_json)
    nodes = graph["nodes"]
    edges = graph["edges"]
    findings = []

    sources = _nodes_by_prefix(nodes, "src")
    targets = _nodes_by_prefix(nodes, "tgt")
    patterns = _nodes_by_prefix(nodes, "pat")
    controls = _nodes_by_prefix(nodes, "ctl")
    middleware = _nodes_by_prefix(nodes, "mid")
    waves = [n for n in nodes if n.get("type") == "wave-group"]

    # MC-001: Every source must connect to a pattern or be retire/retain
    for src in sources:
        outgoing = _edge_targets(edges, src["id"])
        connected_to_pattern = any(
            _node_by_id(nodes, tid) and _node_prefix(_node_by_id(nodes, tid).get("type", "")) == "pat"
            for tid in outgoing
        )
        if not connected_to_pattern:
            findings.append({
                "rule_id": "MC-001",
                "title": MC_COMPLIANCE_RULES[0]["title"],
                "severity": "CAT1",
                "category": "completeness",
                "description": MC_COMPLIANCE_RULES[0]["description"],
                "detail": f"Source '{src.get('label', src['id'])}' has no migration pattern assigned.",
            })

    # MC-002: Pattern must connect to at least one target
    for pat in patterns:
        outgoing = _edge_targets(edges, pat["id"])
        connected_to_target = any(
            _node_by_id(nodes, tid) and _node_prefix(_node_by_id(nodes, tid).get("type", "")) == "tgt"
            for tid in outgoing
        )
        if not connected_to_target:
            findings.append({
                "rule_id": "MC-002",
                "title": MC_COMPLIANCE_RULES[1]["title"],
                "severity": "CAT1",
                "category": "completeness",
                "description": MC_COMPLIANCE_RULES[1]["description"],
                "detail": f"Pattern '{pat.get('label', pat['id'])}' has no target connected.",
            })

    # MC-003: ATO gate required for GovCloud targets
    govcloud_targets = [t for t in targets if t.get("type") == "tgt-govcloud"]
    has_ato_gate = any(c.get("type") == "ctl-ato-gate" for c in controls)
    if govcloud_targets and not has_ato_gate:
        findings.append({
            "rule_id": "MC-003",
            "title": MC_COMPLIANCE_RULES[2]["title"],
            "severity": "CAT1",
            "category": "compliance",
            "description": MC_COMPLIANCE_RULES[2]["description"],
            "detail": f"{len(govcloud_targets)} GovCloud target(s) found but no ATO gate in design.",
        })

    # MC-004: Compliance bridge for 3+ sources
    if len(sources) >= 3:
        has_bridge = any(c.get("type") == "ctl-compliance-bridge" for c in controls)
        if not has_bridge:
            findings.append({
                "rule_id": "MC-004",
                "title": MC_COMPLIANCE_RULES[3]["title"],
                "severity": "CAT2",
                "category": "compliance",
                "description": MC_COMPLIANCE_RULES[3]["description"],
                "detail": f"{len(sources)} source systems found — compliance bridge recommended.",
            })

    # MC-005: Rollback point for non-incremental patterns
    big_bang_patterns = [p for p in patterns if p.get("type") in ("pat-rehost", "pat-rearchitect")]
    has_rollback = any(c.get("type") == "ctl-rollback" for c in controls)
    if big_bang_patterns and not has_rollback:
        findings.append({
            "rule_id": "MC-005",
            "title": MC_COMPLIANCE_RULES[4]["title"],
            "severity": "CAT2",
            "category": "risk",
            "description": MC_COMPLIANCE_RULES[4]["description"],
            "detail": "Rehost/rearchitect patterns detected without rollback checkpoint.",
        })

    # MC-006: Data migration path for database sources
    db_sources = [s for s in sources if s.get("type") in ("src-database",)]
    has_etl = any(m.get("type") in ("mid-etl", "mid-sync") for m in middleware)
    if db_sources and not has_etl:
        findings.append({
            "rule_id": "MC-006",
            "title": MC_COMPLIANCE_RULES[5]["title"],
            "severity": "CAT2",
            "category": "completeness",
            "description": MC_COMPLIANCE_RULES[5]["description"],
            "detail": f"{len(db_sources)} database source(s) found but no ETL/sync pipeline.",
        })

    # MC-007: Security scan before migration
    has_scan = any(c.get("type") == "ctl-security-scan" for c in controls)
    if patterns and not has_scan:
        findings.append({
            "rule_id": "MC-007",
            "title": MC_COMPLIANCE_RULES[6]["title"],
            "severity": "CAT3",
            "category": "security",
            "description": MC_COMPLIANCE_RULES[6]["description"],
            "detail": "No security scan control found in migration design.",
        })

    # MC-008: Test gate after migration
    has_test_gate = any(c.get("type") == "ctl-test-gate" for c in controls)
    if patterns and not has_test_gate:
        findings.append({
            "rule_id": "MC-008",
            "title": MC_COMPLIANCE_RULES[7]["title"],
            "severity": "CAT3",
            "category": "quality",
            "description": MC_COMPLIANCE_RULES[7]["description"],
            "detail": "No test gate found — add post-migration validation.",
        })

    # MC-009: Wave grouping for 5+ sources
    if len(sources) >= 5 and not waves:
        findings.append({
            "rule_id": "MC-009",
            "title": MC_COMPLIANCE_RULES[8]["title"],
            "severity": "CAT3",
            "category": "planning",
            "description": MC_COMPLIANCE_RULES[8]["description"],
            "detail": f"{len(sources)} source systems — wave grouping recommended.",
        })

    # MC-010: ACL for strangler fig
    strangler_patterns = [p for p in patterns if p.get("type") == "pat-strangler"]
    has_acl = any(m.get("type") == "mid-acl" for m in middleware)
    if strangler_patterns and not has_acl:
        findings.append({
            "rule_id": "MC-010",
            "title": MC_COMPLIANCE_RULES[9]["title"],
            "severity": "CAT3",
            "category": "architecture",
            "description": MC_COMPLIANCE_RULES[9]["description"],
            "detail": "Strangler fig pattern detected without anti-corruption layer.",
        })

    # Scoring
    cat1 = sum(1 for f in findings if f["severity"] == "CAT1")
    cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
    cat3 = sum(1 for f in findings if f["severity"] == "CAT3")

    # Deduct: CAT1=-20, CAT2=-10, CAT3=-5
    score = max(0, 100 - (cat1 * 20) - (cat2 * 10) - (cat3 * 5))

    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {
        "findings": findings,
        "score": score,
        "grade": grade,
        "cat1_count": cat1,
        "cat2_count": cat2,
        "cat3_count": cat3,
    }


# ── Gap Detection ───────────────────────────────────────────────────────────


def detect_migration_gaps(graph_json: Any) -> list:
    """Detect architectural and compliance gaps in a migration design.

    Returns list of gap dicts with type, severity, description, recommendation.
    """
    graph = _parse_graph(graph_json)
    nodes = graph["nodes"]
    edges = graph["edges"]
    gaps = []

    sources = _nodes_by_prefix(nodes, "src")
    targets = _nodes_by_prefix(nodes, "tgt")
    patterns = _nodes_by_prefix(nodes, "pat")

    # Orphan nodes — no edges at all
    connected_ids = set()
    for e in edges:
        connected_ids.add(e.get("source", ""))
        connected_ids.add(e.get("target", ""))

    for n in nodes:
        if n["id"] not in connected_ids:
            gaps.append({
                "type": "orphan_node",
                "severity": "high",
                "description": f"Node '{n.get('label', n['id'])}' ({n.get('type', '?')}) is not connected to any other node.",
                "recommendation": "Connect this node to the migration flow or remove it.",
            })

    # Source without pattern
    for src in sources:
        outgoing = _edge_targets(edges, src["id"])
        has_pattern = any(
            _node_by_id(nodes, tid) and _node_prefix(_node_by_id(nodes, tid).get("type", "")) == "pat"
            for tid in outgoing
        )
        if not has_pattern:
            gaps.append({
                "type": "unmapped_source",
                "severity": "high",
                "description": f"Source '{src.get('label', src['id'])}' has no migration strategy assigned.",
                "recommendation": "Connect this source to a migration pattern (Rehost, Refactor, etc.).",
            })

    # Target without incoming pattern
    for tgt in targets:
        incoming = _edge_sources(edges, tgt["id"])
        has_pattern_incoming = any(
            _node_by_id(nodes, sid) and _node_prefix(_node_by_id(nodes, sid).get("type", "")) == "pat"
            for sid in incoming
        )
        if not has_pattern_incoming:
            gaps.append({
                "type": "unmapped_target",
                "severity": "medium",
                "description": f"Target '{tgt.get('label', tgt['id'])}' has no migration pattern feeding into it.",
                "recommendation": "Connect a migration pattern to this target.",
            })

    # Pattern with no source or no target
    for pat in patterns:
        incoming = _edge_sources(edges, pat["id"])
        outgoing = _edge_targets(edges, pat["id"])
        if not incoming:
            gaps.append({
                "type": "pattern_no_source",
                "severity": "medium",
                "description": f"Pattern '{pat.get('label', pat['id'])}' has no source connected.",
                "recommendation": "Connect source systems to this migration pattern.",
            })
        if not outgoing:
            gaps.append({
                "type": "pattern_no_target",
                "severity": "medium",
                "description": f"Pattern '{pat.get('label', pat['id'])}' has no target connected.",
                "recommendation": "Connect target environments to this migration pattern.",
            })

    return gaps


# ── Readiness Score ─────────────────────────────────────────────────────────


def compute_readiness_score(graph_json: Any) -> dict:
    """Calculate migration readiness on 0-100 scale across dimensions.

    Returns:
        {
            "overall": float,
            "completeness": float,
            "compliance": float,
            "risk_mitigation": float,
            "planning": float,
            "summary": str,
        }
    """
    graph = _parse_graph(graph_json)
    nodes = graph["nodes"]
    edges = graph["edges"]

    sources = _nodes_by_prefix(nodes, "src")
    controls = _nodes_by_prefix(nodes, "ctl")
    waves = [n for n in nodes if n.get("type") == "wave-group"]

    if not nodes:
        return {"overall": 0, "completeness": 0, "compliance": 0,
                "risk_mitigation": 0, "planning": 0, "summary": "Empty design — add migration elements."}

    # Completeness: sources mapped + targets connected
    mapped_sources = 0
    for src in sources:
        outgoing = _edge_targets(edges, src["id"])
        if any(_node_by_id(nodes, tid) and _node_prefix(_node_by_id(nodes, tid).get("type", "")) == "pat" for tid in outgoing):
            mapped_sources += 1
    completeness = (mapped_sources / max(len(sources), 1)) * 100

    # Compliance: controls present
    control_types_present = {c.get("type") for c in controls}
    desired_controls = {"ctl-ato-gate", "ctl-compliance-bridge", "ctl-security-scan", "ctl-test-gate", "ctl-rollback"}
    compliance = (len(control_types_present & desired_controls) / len(desired_controls)) * 100

    # Risk mitigation: rollback + security scan + test gate
    risk_items = {"ctl-rollback", "ctl-security-scan", "ctl-test-gate"}
    risk_mitigation = (len(control_types_present & risk_items) / len(risk_items)) * 100

    # Planning: waves + milestones
    planning_score = 50.0  # base
    if waves:
        planning_score += 25.0
    if any(n.get("type") == "plan-milestone" for n in nodes):
        planning_score += 25.0

    overall = (completeness * 0.35 + compliance * 0.25 + risk_mitigation * 0.20 + planning_score * 0.20)

    if overall >= 80:
        summary = "Migration design is well-prepared — proceed with execution."
    elif overall >= 60:
        summary = "Design has gaps — address compliance and risk controls before proceeding."
    elif overall >= 40:
        summary = "Significant gaps detected — complete source mapping and add compliance gates."
    else:
        summary = "Design is incomplete — map all sources and add minimum viable controls."

    return {
        "overall": round(overall, 1),
        "completeness": round(completeness, 1),
        "compliance": round(compliance, 1),
        "risk_mitigation": round(risk_mitigation, 1),
        "planning": round(planning_score, 1),
        "summary": summary,
    }


# ── Design Statistics ───────────────────────────────────────────────────────


def get_design_stats(graph_json: Any) -> dict:
    """Return summary statistics for a migration design."""
    graph = _parse_graph(graph_json)
    nodes = graph["nodes"]
    edges = graph["edges"]

    sources = _nodes_by_prefix(nodes, "src")
    targets = _nodes_by_prefix(nodes, "tgt")
    patterns = _nodes_by_prefix(nodes, "pat")
    controls = _nodes_by_prefix(nodes, "ctl")

    # Strategy distribution
    strategy_counts = {}
    for p in patterns:
        ptype = p.get("type", "unknown")
        strategy_counts[ptype] = strategy_counts.get(ptype, 0) + 1

    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "sources": len(sources),
        "targets": len(targets),
        "patterns": len(patterns),
        "controls": len(controls),
        "strategy_distribution": strategy_counts,
    }
