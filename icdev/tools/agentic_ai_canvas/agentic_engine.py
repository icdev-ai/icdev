# CUI // SP-CTI
"""Agentic AI Design Canvas — Rule-based compliance assessment engine.

Evaluates a design graph JSON against:
  - NIST AI RMF (Govern / Map / Measure / Manage)
  - OWASP LLM Top 10
  - OMB M-25-21 (rights/safety-impacting classification)
  - Autonomy Level Classification (L0–L5 per agent node)
  - MITRE ATLAS threat mapping (adversarial ML technique identification)

All checks are deterministic and rule-based — no LLM calls required.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.agentic_ai_canvas.constants import (
    AGENT_NODES,
    ATLAS_THREAT_MAP,
    AUTONOMY_LEVELS,
    NIST_AI_RMF_CHECKS,
    OWASP_LLM_CHECKS,
    OUTPUT_NODES,
    P4_EXECUTION_CHECKS,
    RIGHTS_IMPACTING_DOMAINS,
    SAFETY_IMPACTING_DOMAINS,
)
from tools.agentic_ai_canvas.observability_nodes import check_observability_coverage
from tools.agentic_ai_canvas.a2a_sandbox import check_a2a_sandbox
from tools.agentic_ai_canvas.safety_extensions import check_safety_extensions
from tools.agentic_ai_canvas.memory_layer import check_memory_layer
from tools.agentic_ai_canvas.confidence_gate import check_confidence_gate_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_graph(graph_json: str | dict) -> tuple[list[dict], list[dict]]:
    if isinstance(graph_json, str):
        g = json.loads(graph_json)
    else:
        g = graph_json
    return g.get("nodes", []), g.get("edges", [])


def _node_types(nodes: list[dict]) -> set[str]:
    return {n.get("type", "") for n in nodes}


def _build_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        adj.setdefault(s, set()).add(t)
    return adj


def _build_reverse_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    radj: dict[str, set[str]] = {}
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        radj.setdefault(t, set()).add(s)
    return radj


def _reachable(start: str, adj: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        stack.extend(adj.get(n, set()) - visited)
    return visited


# ---------------------------------------------------------------------------
# Autonomy Level Classification
# ---------------------------------------------------------------------------

def classify_autonomy(agent_node: dict, nodes: list[dict], edges: list[dict]) -> int:
    """Return autonomy level 0–5 for a single agent node."""
    adj = _build_adjacency(edges)
    reachable = _reachable(agent_node["id"], adj)
    reachable_types = {n.get("type") for n in nodes if n["id"] in reachable}

    has_hitl = bool(reachable_types & {"hitl-gate", "approval-workflow", "caio-override"})
    has_circuit_breaker = "circuit-breaker" in reachable_types
    has_confidence = "confidence-threshold" in reachable_types
    has_audit = "audit-logger" in reachable_types

    agent_type = agent_node.get("type", "")

    if agent_type == "semi-auto-agent":
        return 1 if has_hitl else 2
    if agent_type == "autonomous-agent":
        if not has_circuit_breaker and not has_hitl and not has_confidence:
            return 5  # Unconstrained
        if has_hitl:
            return 1
        if has_circuit_breaker and has_confidence and has_audit:
            return 4
        if has_circuit_breaker:
            return 3
        return 2
    if agent_type == "orchestrator":
        if has_hitl:
            return 2
        if has_circuit_breaker:
            return 3
        return 4
    # sub-agent, researcher, writer, analyst, reviewer
    return 1 if has_hitl else 2


# ---------------------------------------------------------------------------
# OMB M-25-21 classification
# ---------------------------------------------------------------------------

def classify_impact(design_meta: dict) -> tuple[bool, bool]:
    """Return (safety_impacting, rights_impacting) based on design domain tag."""
    domain = (design_meta.get("domain") or "").lower()
    domains = {d.strip() for d in domain.split(",")}
    safety = bool(domains & SAFETY_IMPACTING_DOMAINS)
    rights = bool(domains & RIGHTS_IMPACTING_DOMAINS)
    return safety, rights


# ---------------------------------------------------------------------------
# HITL path verifier
# ---------------------------------------------------------------------------

def verify_hitl_paths(nodes: list[dict], edges: list[dict],
                      safety_impacting: bool) -> list[dict]:
    """If safety/rights-impacting, verify every path from agent → output has a HITL gate."""
    if not safety_impacting:
        return []

    findings: list[dict] = []
    adj = _build_adjacency(edges)
    node_map = {n["id"]: n for n in nodes}

    agent_ids = [n["id"] for n in nodes if n.get("type") in AGENT_NODES]
    for agent_id in agent_ids:
        reachable = _reachable(agent_id, adj)
        has_hitl = any(node_map.get(r, {}).get("type") in {"hitl-gate", "approval-workflow"}
                       for r in reachable)
        if not has_hitl:
            findings.append({
                "id": f"hitl-path-{uuid.uuid4().hex[:6]}",
                "framework": "OMB M-25-21",
                "severity": "HIGH",
                "title": "Safety-impacting agent lacks HITL gate",
                "detail": f"Agent node '{node_map.get(agent_id, {}).get('label', agent_id)}' "
                          "has no hitl-gate or approval-workflow on any downstream path.",
                "recommendation": "Add a HITL Gate node downstream of this agent.",
            })
    return findings


# ---------------------------------------------------------------------------
# NIST AI RMF checks
# ---------------------------------------------------------------------------

def check_nist_ai_rmf(nodes: list[dict], edges: list[dict],
                      design_meta: dict) -> tuple[list[dict], float]:
    types = _node_types(nodes)
    findings: list[dict] = []
    adj = _build_adjacency(edges)
    node_map = {n["id"]: n for n in nodes}

    total_weight = sum(c["weight"] for c in NIST_AI_RMF_CHECKS)
    passed_weight = 0.0

    for check in NIST_AI_RMF_CHECKS:
        passed = False

        if "required_any" in check:
            passed = bool(types & set(check["required_any"]))

        elif check.get("meta_field"):
            passed = bool(design_meta.get(check["meta_field"]))

        elif check.get("check") == "has_prior_assessment":
            passed = design_meta.get("has_prior_assessment", False)

        elif check.get("check") == "agent_has_circuit_breaker":
            agent_ids = [n["id"] for n in nodes if n.get("type") in AGENT_NODES]
            if not agent_ids:
                passed = True  # No agents → no requirement
            else:
                all_ok = True
                for aid in agent_ids:
                    reachable = _reachable(aid, adj)
                    rt = {node_map.get(r, {}).get("type") for r in reachable}
                    if "circuit-breaker" not in rt and "hitl-gate" not in rt:
                        all_ok = False
                        break
                passed = all_ok

        elif check.get("check") == "ai_use_case_registered":
            # NIST AI RMF 2.0 GOVERN-1.7: system-card/AI-BOM node OR use_case_id metadata
            passed = (
                bool(types & {"system-card", "ai-bom", "gov-system-card", "gov-ai-bom"})
                or bool(design_meta.get("use_case_id"))
                or bool(design_meta.get("system_card"))
            )

        elif check.get("required_output"):
            passed = bool(types & {"inference-input"}) and bool(types & OUTPUT_NODES)

        if passed:
            passed_weight += check["weight"]
        else:
            findings.append({
                "id": f"rmf-{check['id']}",
                "framework": "NIST AI RMF",
                "function": check["function"],
                "category": check["category"],
                "severity": "MEDIUM" if check["weight"] < 12 else "HIGH",
                "title": check["title"],
                "detail": check["description"],
                "recommendation": f"Add required node(s): {check.get('required_any', check.get('check', 'see description'))}",
            })

    score = round((passed_weight / total_weight) * 100, 1) if total_weight else 0.0
    return findings, score


# ---------------------------------------------------------------------------
# OWASP LLM Top 10 checks
# ---------------------------------------------------------------------------

def check_owasp_llm(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], float]:
    types = _node_types(nodes)
    findings: list[dict] = []
    adj = _build_adjacency(edges)
    radj = _build_reverse_adjacency(edges)
    node_map = {n["id"]: n for n in nodes}

    total_weight = sum(c["weight"] for c in OWASP_LLM_CHECKS)
    passed_weight = 0.0

    has_llm = bool(types & {"llm", "llm-local"})
    has_mcp = bool(types & {"mcp-server"})
    has_agent = bool(types & AGENT_NODES)
    has_training = "training-data" in types

    for check in OWASP_LLM_CHECKS:
        passed = False

        if "required_any" in check:
            passed = bool(types & set(check["required_any"]))
        elif "required_all" in check:
            passed = set(check["required_all"]).issubset(types)

        elif check["id"] == "llm01":
            if not has_llm:
                passed = True
            else:
                llm_ids = [n["id"] for n in nodes if n.get("type") in {"llm", "llm-local"}]
                passed = all(
                    any(node_map.get(u, {}).get("type") == "input-sanitizer"
                        for u in radj.get(lid, set()))
                    for lid in llm_ids
                )

        elif check["id"] == "llm02":
            if not has_llm:
                passed = True
            else:
                llm_ids = [n["id"] for n in nodes if n.get("type") in {"llm", "llm-local"}]
                passed = all(
                    any(node_map.get(d, {}).get("type") == "output-validator"
                        for d in _reachable(lid, adj))
                    for lid in llm_ids
                )

        elif check["id"] == "llm03":
            if not has_training:
                passed = True
            else:
                td_ids = [n["id"] for n in nodes if n.get("type") == "training-data"]
                passed = all(
                    any(node_map.get(d, {}).get("type") == "audit-logger"
                        for d in _reachable(tid, adj))
                    for tid in td_ids
                )

        elif check["id"] == "llm05":
            if not has_llm:
                passed = True
            else:
                passed = "model-registry" in types

        elif check["id"] == "llm07":
            if not has_mcp:
                passed = True
            else:
                mcp_ids = [n["id"] for n in nodes if n.get("type") == "mcp-server"]
                passed = all(
                    any(node_map.get(u, {}).get("type") == "mcp-gateway"
                        for u in radj.get(mid, set()))
                    for mid in mcp_ids
                )

        elif check["id"] == "llm08":
            if not has_agent:
                passed = True
            else:
                agent_ids = [n["id"] for n in nodes if n.get("type") in AGENT_NODES]
                passed = all(
                    "circuit-breaker" in {node_map.get(d, {}).get("type")
                                          for d in _reachable(aid, adj)}
                    or "hitl-gate" in {node_map.get(d, {}).get("type")
                                       for d in _reachable(aid, adj)}
                    for aid in agent_ids
                )

        if passed:
            passed_weight += check["weight"]
        else:
            findings.append({
                "id": f"owasp-{check['id']}",
                "framework": "OWASP LLM Top 10",
                "risk_id": check["id"].upper(),
                "severity": "HIGH" if check["weight"] >= 12 else "MEDIUM",
                "title": check["title"],
                "detail": check["description"],
                "recommendation": f"See OWASP LLM {check['id'].upper()} remediation guidance.",
            })

    score = round((passed_weight / total_weight) * 100, 1) if total_weight else 0.0
    return findings, score


# ---------------------------------------------------------------------------
# MITRE ATLAS threat mapping
# ---------------------------------------------------------------------------

def map_atlas_threats(nodes: list[dict]) -> list[dict]:
    threats: list[dict] = []
    seen: set[str] = set()
    for node in nodes:
        ntype = node.get("type", "")
        for technique in ATLAS_THREAT_MAP.get(ntype, []):
            if technique not in seen:
                seen.add(technique)
                threats.append({
                    "technique": technique,
                    "node_type": ntype,
                    "node_label": node.get("label", ntype),
                })
    return threats


# ---------------------------------------------------------------------------
# Phase 4 — execution node check
# ---------------------------------------------------------------------------

def _check_execution_nodes(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Advisory check: multi-agent designs benefit from checkpoint nodes."""
    types = _node_types(nodes)
    agent_count = sum(1 for n in nodes if n.get("type") in AGENT_NODES)
    findings: list[dict] = []

    for check in P4_EXECUTION_CHECKS:
        if check["check"] == "has_checkpoint_node":
            if agent_count < 2:
                continue  # only relevant for multi-agent designs
            if "checkpoint" not in types:
                findings.append({
                    "id": f"p4-{check['id']}",
                    "framework": "NIST AI RMF (Phase 4)",
                    "function": check["function"],
                    "category": check["category"],
                    "severity": "LOW",
                    "title": check["title"],
                    "detail": check["description"],
                    "recommendation": "Add a checkpoint node to enable state recovery on agent failure.",
                })
    return findings


# ---------------------------------------------------------------------------
# Full design assessment
# ---------------------------------------------------------------------------

def assess_design(design_id: str, graph_json: str | dict,
                  design_meta: dict | None = None) -> dict:
    """Run full assessment. Returns findings dict ready for aadc_assessments insert."""
    meta = design_meta or {}
    nodes, edges = _parse_graph(graph_json)

    # Classify impact
    safety_impacting, rights_impacting = classify_impact(meta)

    # Autonomy levels
    agent_nodes = [n for n in nodes if n.get("type") in AGENT_NODES]
    autonomy_levels = [classify_autonomy(a, nodes, edges) for a in agent_nodes]
    autonomy_max = max(autonomy_levels, default=0)

    # Run core checks
    rmf_findings, rmf_score = check_nist_ai_rmf(nodes, edges, {
        **meta, "has_prior_assessment": meta.get("has_prior_assessment", False)
    })
    owasp_findings, owasp_score = check_owasp_llm(nodes, edges)
    hitl_findings = verify_hitl_paths(nodes, edges, safety_impacting or rights_impacting)
    atlas_threats = map_atlas_threats(nodes)

    # Phase 4 checks
    obs_findings, obs_score = check_observability_coverage(nodes, edges)
    a2a_findings, a2a_score = check_a2a_sandbox(nodes, edges)
    safety_ext_findings, safety_ext_score = check_safety_extensions(nodes, edges)
    mem_findings, mem_score = check_memory_layer(nodes, edges)

    # Phase 4 execution check (bonus — not penalised if no agents)
    p4_exec_findings = _check_execution_nodes(nodes, edges)

    # Confidence gate path check (MEA-1)
    cg_findings = check_confidence_gate_path(nodes, edges)

    # AIMC bridge: IL compatibility check + OWASP LLM01 bridge security check.
    # If the bridge is unavailable (import/DB error) its findings would silently
    # vanish; record an explicit marker so the assessment output distinguishes
    # "bridge ran, found nothing" from "bridge never ran".
    il_compat_findings: list[dict] = []
    bridge_sec_findings: list[dict] = []
    bridge_status = "available"
    try:
        from tools.agentic_ai_canvas.canvas_bridge import check_il_compatibility, bridge_security_check
        violations = check_il_compatibility(design_id)
        for v in violations:
            il_compat_findings.append({
                "id": f"il-compat-{v.get('aadc_node_id', 'unknown')[:8]}",
                "framework": "AIMC IL Suitability",
                "severity": v.get("severity", "CAT1"),
                "title": f"IL_MISMATCH: {v.get('aimc_model_id', '?')} on AADC node",
                "detail": "; ".join(v.get("issues", [v.get("issue", "IL incompatibility")])),
                "recommendation": "Select a model from AIMC catalog that supports the design's IL level.",
            })
        bridge_sec_findings = bridge_security_check(design_id, nodes, edges)
    except Exception:
        bridge_status = "unavailable"

    all_findings = (rmf_findings + owasp_findings + hitl_findings
                    + obs_findings + a2a_findings + safety_ext_findings
                    + mem_findings + p4_exec_findings + cg_findings
                    + il_compat_findings + bridge_sec_findings)

    # L5 (unconstrained) agent is always a CRITICAL finding
    # L3+ (Human-Initiated) without a HITL gate is a CAT1 finding
    hitl_node_ids = {n["id"] for n in nodes if n.get("type") in ("hitl-gate", "caio-override", "approval-workflow")}
    for a, level in zip(agent_nodes, autonomy_levels):
        if level == 5:
            all_findings.append({
                "id": f"autonomy-l5-{a['id'][:6]}",
                "framework": "NIST AI RMF",
                "severity": "CRITICAL",
                "title": "Unconstrained autonomous agent (L5)",
                "detail": f"Agent '{a.get('label', a['id'])}' has no circuit breaker, HITL gate, or confidence threshold.",
                "recommendation": "Add circuit-breaker, confidence-threshold, and audit-logger nodes downstream.",
            })
        elif level >= 3:
            # Check if any downstream edge connects to a HITL gate
            agent_downstream = {e["target"] for e in edges if e.get("source") == a["id"]}
            has_hitl_downstream = bool(agent_downstream & hitl_node_ids)
            if not has_hitl_downstream:
                all_findings.append({
                    "id": f"autonomy-l3plus-{a['id'][:6]}",
                    "framework": "NIST AI RMF",
                    "severity": "CAT1",
                    "title": f"Autonomy L{level} agent without mandatory HITL gate",
                    "detail": f"Agent '{a.get('label', a['id'])}' at L{level} ({AUTONOMY_LEVELS.get(level)}) has no HITL gate or approval-workflow downstream. DoD policy requires human oversight for L3+ autonomous systems.",
                    "recommendation": "Add a hitl-gate or approval-workflow node on the agent's output path.",
                })

    # OMB M-25-21 compliance flag
    omb_compliant = int((safety_impacting or rights_impacting) and not hitl_findings)

    # Aggregate score:
    #   Core: 40% NIST RMF + 40% OWASP  = 80%
    #   Phase 4 (optional bonus when applicable): up to 20% from obs/a2a/safety-ext/mem
    p4_scores = [s for s in (obs_score, a2a_score, safety_ext_score, mem_score)
                 if s is not None]
    if p4_scores:
        p4_avg = sum(p4_scores) / len(p4_scores)
        score = round((rmf_score * 0.40) + (owasp_score * 0.40) + (p4_avg * 0.20), 1)
    else:
        score = round((rmf_score * 0.50) + (owasp_score * 0.50), 1)

    return {
        "id": f"asmnt-{uuid.uuid4().hex[:10]}",
        "design_id": design_id,
        "score": score,
        "nist_rmf_score": rmf_score,
        "owasp_score": owasp_score,
        "obs_score": obs_score,
        "a2a_score": a2a_score,
        "safety_ext_score": safety_ext_score,
        "mem_score": mem_score,
        "omb_compliant": omb_compliant,
        "autonomy_max": autonomy_max,
        "safety_impacting": int(safety_impacting),
        "rights_impacting": int(rights_impacting),
        "findings_json": json.dumps(all_findings),
        "atlas_threats": json.dumps(atlas_threats),
        "bridge": bridge_status,
        "created_at": _now(),
    }
