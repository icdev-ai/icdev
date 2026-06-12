# CUI // SP-CTI
"""Agentic AI Design Canvas — Auto-Recommendation / Design Linter (Phase 7).

Scans the design graph for structural gaps and produces per-node and
per-topology recommendations. Used to drive the canvas lint overlay.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import (
    AGENT_NODES,
    GOVERNANCE_NODES,
    MODEL_NODES,
    SAFETY_NODES,
    TOOL_MCP_NODES,
)

_HITL = {"hitl-gate", "approval-workflow", "caio-override"}
_INPUT_GUARDS = {"guardrail", "input-sanitizer", "toxicity-filter"}
_OUTPUT_GUARDS = {"guardrail", "toxicity-filter"}
_PII = {"pii-detector", "pii-field-detector", "redaction-engine"}
_MONITOR = {"trusted-monitor", "drift-detector", "baseline-snapshot"}
_AUDIT = {"audit-logger", "compliance-reporter"}
_CB = {"circuit-breaker", "confidence-threshold"}
_SANDBOX = {"sandbox-exec"}
_RATE = {"rate-limiter"}
_MEMORY = {"vector-db", "knowledge-graph", "episodic-memory", "working-memory"}


def lint_design(nodes: list[dict], edges: list[dict], design_meta: dict) -> dict:  # noqa: ARG001
    """
    Scan design for structural gaps.

    Returns dict with keys:
        recommendations: list of {id, severity, category, message, add_node_type, node_ids}
        node_warnings: {node_id: [warning_message, ...]}
        lint_score: float 0-100 (100 = no warnings)
    """
    types = {n.get("type", "") for n in nodes}
    node_by_id = {n["id"]: n for n in nodes}

    # Build adjacency for simple upstream check
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    radj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        if src in adj:
            adj[src].append(tgt)
        if tgt in radj:
            radj[tgt].append(src)

    recs: list[dict] = []
    node_warnings: dict[str, list[str]] = {}

    def _warn(node_id: str, msg: str) -> None:
        node_warnings.setdefault(node_id, []).append(msg)

    def _rec(rid: str, severity: str, category: str, message: str,
             add_node_type: str = "", node_ids: list[str] | None = None) -> None:
        recs.append({
            "id": rid,
            "severity": severity,
            "category": category,
            "message": message,
            "add_node_type": add_node_type,
            "node_ids": node_ids or [],
        })

    # ── Topology-level checks ──────────────────────────────────────────────

    has_models = bool(types & MODEL_NODES)
    has_agents = bool(types & AGENT_NODES)
    has_tools = bool(types & TOOL_MCP_NODES)
    has_memory = bool(types & _MEMORY)

    if has_models and not (types & _INPUT_GUARDS):
        _rec("LNT-01", "HIGH", "Safety",
             "LLM nodes present with no input validation — prompt injection risk.",
             add_node_type="input-sanitizer")

    if has_models and not (types & _OUTPUT_GUARDS):
        _rec("LNT-02", "HIGH", "Safety",
             "LLM nodes present with no output filtering — toxic/harmful output risk.",
             add_node_type="toxicity-filter")

    if has_agents and not (types & _HITL):
        _rec("LNT-03", "HIGH", "Governance",
             "Autonomous agents present with no human-in-the-loop gate.",
             add_node_type="hitl-gate")

    if has_agents and not (types & _AUDIT):
        _rec("LNT-04", "HIGH", "Compliance",
             "Agent activity is not logged — add an Audit Logger for AU-2 compliance.",
             add_node_type="audit-logger")

    if has_agents and not (types & _CB):
        _rec("LNT-05", "MEDIUM", "Reliability",
             "No circuit breaker detected — agents may cascade on failure.",
             add_node_type="circuit-breaker")

    if has_memory and not (types & _PII):
        _rec("LNT-06", "HIGH", "Privacy",
             "Memory/vector store present with no PII detection — PII exfiltration risk.",
             add_node_type="pii-detector")

    if has_tools and not (types & _SANDBOX) and not (types & {"mcp-gateway"}):
        _rec("LNT-07", "HIGH", "Security",
             "Tool/MCP nodes present without sandbox or gateway — unsandboxed code execution risk.",
             add_node_type="sandbox-exec")

    if has_models and not (types & _MONITOR):
        _rec("LNT-08", "MEDIUM", "Observability",
             "No monitoring/drift detection — model degradation will go undetected.",
             add_node_type="trusted-monitor")

    if has_agents and not (types & _RATE):
        _rec("LNT-09", "MEDIUM", "Reliability",
             "No rate limiter — agents may be exploited for resource exhaustion.",
             add_node_type="rate-limiter")

    if has_agents and not (types & GOVERNANCE_NODES):
        _rec("LNT-10", "MEDIUM", "Governance",
             "No governance node (CAIO Override, Compliance Reporter) — DoD AI Ethics gap.",
             add_node_type="compliance-reporter")

    autonomy = design_meta.get("autonomy_max", 0) or 0
    if autonomy >= 4 and not (types & _HITL):
        _rec("LNT-11", "CRITICAL", "Safety",
             f"Autonomy level L{autonomy} with no HITL gate — OMB M-25-21 violation for high-autonomy AI.",
             add_node_type="hitl-gate")

    if design_meta.get("safety_impacting") and not (types & _HITL):
        _rec("LNT-12", "CRITICAL", "Compliance",
             "Safety-impacting domain with no HITL gate — OMB M-25-21 §4(a) violation.",
             add_node_type="hitl-gate")

    # ── Per-node checks ────────────────────────────────────────────────────

    for node in nodes:
        ntype = node.get("type", "")
        nid = node.get("id", "")
        label = node.get("label", nid)

        # Model nodes without provenance
        if ntype in MODEL_NODES:
            meta = node.get("metadata") or {}
            if not (meta.get("model_source") or node.get("model_source")):
                _warn(nid, f"'{label}': model_source not documented (supply chain risk)")
                if not any(r["id"] == "LNT-13" for r in recs):
                    _rec("LNT-13", "MEDIUM", "Supply Chain",
                         "One or more model nodes lack model_source documentation.",
                         node_ids=[n["id"] for n in nodes if n.get("type") in MODEL_NODES
                                   and not ((n.get("metadata") or {}).get("model_source") or n.get("model_source"))])

        # Agent nodes with no safety predecessors
        if ntype in AGENT_NODES:
            upstream_types = {node_by_id[pid].get("type", "") for pid in radj.get(nid, []) if pid in node_by_id}
            if not (upstream_types & (SAFETY_NODES | GOVERNANCE_NODES | _HITL)):
                _warn(nid, f"'{label}': agent has no safety/governance predecessor — unprotected execution")

        # LLM nodes with no predecessor guard
        if ntype in MODEL_NODES:
            upstream_types = {node_by_id[pid].get("type", "") for pid in radj.get(nid, []) if pid in node_by_id}
            if not (upstream_types & _INPUT_GUARDS) and any(
                node_by_id.get(pid, {}).get("type") not in MODEL_NODES
                for pid in radj.get(nid, []) if pid in node_by_id
            ):
                _warn(nid, f"'{label}': LLM receives input without an upstream guardrail")

    # ── Score ──────────────────────────────────────────────────────────────

    sev_weight = {"CRITICAL": 20, "HIGH": 10, "MEDIUM": 5, "LOW": 2}
    penalty = sum(sev_weight.get(r["severity"], 0) for r in recs)
    lint_score = max(0.0, round(100.0 - penalty, 1))

    return {
        "recommendations": recs,
        "node_warnings": node_warnings,
        "lint_score": lint_score,
        "summary": {
            "total": len(recs),
            "critical": sum(1 for r in recs if r["severity"] == "CRITICAL"),
            "high": sum(1 for r in recs if r["severity"] == "HIGH"),
            "medium": sum(1 for r in recs if r["severity"] == "MEDIUM"),
            "nodes_with_warnings": len(node_warnings),
            "lint_score": lint_score,
        },
    }
