# CUI // SP-CTI
"""Agentic AI Design Canvas — STRIDE + ATLAS Threat Model Generator (Phase 5).

Generates a structured threat model from an AADC design graph.
STRIDE: Spoofing / Tampering / Repudiation / Information Disclosure / DoS / Elevation of Privilege.
ATLAS: Maps MITRE ATLAS techniques to node types (re-uses ATLAS_THREAT_MAP from constants).
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import (
    AGENT_NODES,
    ATLAS_THREAT_MAP,
    DATA_NODES,
    GOVERNANCE_NODES,
    INFRA_NODES,
    MODEL_NODES,
    TOOL_MCP_NODES,
)

# STRIDE rules: node type → list of applicable threat categories
_STRIDE_RULES: list[dict] = [
    # Spoofing
    {
        "id": "STR-01", "category": "Spoofing", "severity": "HIGH",
        "applies_to": MODEL_NODES | AGENT_NODES,
        "title": "Agent/Model Identity Spoofing",
        "description": "An attacker impersonates a legitimate AI agent or model endpoint, injecting malicious responses.",
        "mitigation": "Implement mutual TLS + agent identity attestation (A2A bridge). Add audit-logger for all agent calls.",
    },
    {
        "id": "STR-02", "category": "Spoofing", "severity": "MEDIUM",
        "applies_to": TOOL_MCP_NODES,
        "title": "MCP Tool Endpoint Spoofing",
        "description": "A rogue MCP server responds to tool calls instead of the legitimate endpoint.",
        "mitigation": "Pin MCP server certificates. Use tool-registry for approved endpoints only.",
    },
    # Tampering
    {
        "id": "TAM-01", "category": "Tampering", "severity": "CRITICAL",
        "applies_to": DATA_NODES,
        "title": "Training Data / RAG Corpus Poisoning",
        "description": "Attacker injects adversarial content into training data or retrieval corpus (OWASP LLM03).",
        "mitigation": "Add data-validator node upstream. Enable cryptographic signatures on ingested documents.",
    },
    {
        "id": "TAM-02", "category": "Tampering", "severity": "HIGH",
        "applies_to": MODEL_NODES,
        "title": "Model Weight Tampering",
        "description": "Attacker modifies model weights at rest in the model registry, introducing backdoors.",
        "mitigation": "Hash-verify model artifacts at load time. Store checksums in model-registry with audit trail.",
    },
    # Repudiation
    {
        "id": "REP-01", "category": "Repudiation", "severity": "HIGH",
        "applies_to": AGENT_NODES | GOVERNANCE_NODES,
        "title": "Agent Action Non-Repudiation",
        "description": "Agent actions (tool calls, decisions, outputs) cannot be traced to a specific invocation.",
        "mitigation": "Require audit-logger downstream of every autonomous-agent and HITL gate.",
    },
    # Information Disclosure
    {
        "id": "INF-01", "category": "Information Disclosure", "severity": "HIGH",
        "applies_to": MODEL_NODES | AGENT_NODES,
        "title": "Prompt Injection / System Prompt Leak",
        "description": "Attacker injects instructions via user input to exfiltrate system prompts or context (OWASP LLM01).",
        "mitigation": "Add input-sanitizer and guardrail nodes. Use prompt-registry to lock system prompts.",
    },
    {
        "id": "INF-02", "category": "Information Disclosure", "severity": "CRITICAL",
        "applies_to": DATA_NODES,
        "title": "PII / Sensitive Data Leakage in Outputs",
        "description": "Model outputs contain PII or classified content from training data or context (OWASP LLM06).",
        "mitigation": "Add pii-detector and redaction-engine nodes downstream of all model outputs.",
    },
    # Denial of Service
    {
        "id": "DOS-01", "category": "Denial of Service", "severity": "MEDIUM",
        "applies_to": AGENT_NODES | TOOL_MCP_NODES,
        "title": "Resource Exhaustion via Agent Loop",
        "description": "Unconstrained agent spawns or tool calls exhaust compute/API quotas.",
        "mitigation": "Add token-budget and rate-limiter nodes. Implement circuit-breaker with threshold.",
    },
    {
        "id": "DOS-02", "category": "Denial of Service", "severity": "MEDIUM",
        "applies_to": INFRA_NODES,
        "title": "GPU/Inference Infrastructure Overload",
        "description": "Adversarial or pathological inputs trigger excessive inference compute.",
        "mitigation": "Add confidence-threshold and rate-limiter. Configure token-budget per session.",
    },
    # Elevation of Privilege
    {
        "id": "EOP-01", "category": "Elevation of Privilege", "severity": "CRITICAL",
        "applies_to": AGENT_NODES,
        "title": "Unauthorized Privilege via Prompt Injection",
        "description": "Injected prompt causes agent to perform actions beyond its authorized scope (OWASP LLM01).",
        "mitigation": "Add HITL gate for high-privilege operations. Implement caio-override for safety-critical paths.",
    },
    {
        "id": "EOP-02", "category": "Elevation of Privilege", "severity": "HIGH",
        "applies_to": TOOL_MCP_NODES,
        "title": "Excessive Tool Permissions",
        "description": "MCP tools or code executors granted permissions beyond minimum required (OWASP LLM09).",
        "mitigation": "Add guardrail and input-sanitizer upstream of code-executor and external-api nodes.",
    },
]


def generate_threat_model(nodes: list[dict], edges: list[dict]) -> dict:
    """Generate a STRIDE + ATLAS threat model from a design graph."""
    # STRIDE threats: apply rules where at least one matching node exists
    stride_threats: list[dict] = []
    for rule in _STRIDE_RULES:
        matching_nodes = [
            n for n in nodes if n.get("type", "") in rule["applies_to"]
        ]
        if matching_nodes:
            stride_threats.append({
                "id": rule["id"],
                "category": rule["category"],
                "severity": rule["severity"],
                "title": rule["title"],
                "description": rule["description"],
                "mitigation": rule["mitigation"],
                "affected_nodes": [{"id": n["id"], "label": n.get("label", n["id"]), "type": n.get("type", "")} for n in matching_nodes[:5]],
            })

    # ATLAS threats: re-use existing map from constants
    atlas_threats: list[dict] = []
    seen_atlas: set[str] = set()
    for n in nodes:
        ntype = n.get("type", "")
        for threat in ATLAS_THREAT_MAP.get(ntype, []):
            key = f"{threat.get('technique_id', '')}"
            if key not in seen_atlas:
                seen_atlas.add(key)
                atlas_threats.append({
                    **threat,
                    "affected_node": n.get("label", n["id"]),
                    "affected_node_id": n["id"],
                })

    high_count = sum(1 for t in stride_threats if t["severity"] in ("CRITICAL", "HIGH"))

    return {
        "stride": stride_threats,
        "atlas": atlas_threats,
        "threat_count": len(stride_threats) + len(atlas_threats),
        "high_count": high_count,
        "stride_summary": _stride_summary(stride_threats),
    }


def _stride_summary(threats: list[dict]) -> dict:
    cats = ["Spoofing", "Tampering", "Repudiation", "Information Disclosure",
            "Denial of Service", "Elevation of Privilege"]
    return {c: sum(1 for t in threats if t["category"] == c) for c in cats}
