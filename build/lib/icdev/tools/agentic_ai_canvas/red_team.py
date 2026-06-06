# CUI // SP-CTI
"""Agentic AI Design Canvas — AI Red Team Engine (Phase 7).

Evaluates a design graph against 12 adversarial attack scenarios mapped
to MITRE ATLAS tactics. Each scenario checks which node types are exposed,
whether mitigating controls exist, and assigns an exploitability score.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import (
    AGENT_NODES,
    MODEL_NODES,
    TOOL_MCP_NODES,
)

# Mitigating node sets
_HITL = {"hitl-gate", "approval-workflow", "caio-override"}
_INPUT_GUARDS = {"guardrail", "input-sanitizer", "toxicity-filter"}
_OUTPUT_GUARDS = {"guardrail", "toxicity-filter"}
_PII = {"pii-detector", "pii-field-detector", "redaction-engine"}
_MONITOR = {"trusted-monitor", "drift-detector", "baseline-snapshot"}
_AUDIT = {"audit-logger", "compliance-reporter"}
_CB = {"circuit-breaker", "confidence-threshold"}
_SANDBOX = {"sandbox-exec"}
_PROMPT_REG = {"prompt-registry"}
_RATE = {"rate-limiter"}

# ATLAS tactic categories
_ATLAS_ML_ATTACK = "ML Attack Staging"
_ATLAS_EXFIL = "Exfiltration"
_ATLAS_IMPACT = "Impact"
_ATLAS_RECON = "Reconnaissance"
_ATLAS_EVASION = "ML Evasion"
_ATLAS_PERSIST = "Persistence"

_SCENARIOS: list[dict] = [
    {
        "id": "RT-01",
        "tactic": _ATLAS_ML_ATTACK,
        "technique": "AML.T0051",
        "title": "Direct Prompt Injection",
        "description": "Attacker embeds adversarial instructions in user input to hijack LLM behavior.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _INPUT_GUARDS,
        "severity": "CRITICAL",
        "base_score": 9.0,
        "mitigation_reduction": 6.0,
    },
    {
        "id": "RT-02",
        "tactic": _ATLAS_ML_ATTACK,
        "technique": "AML.T0054",
        "title": "Indirect Prompt Injection",
        "description": "Malicious instructions embedded in retrieved documents or tool outputs.",
        "requires_node_types": MODEL_NODES | TOOL_MCP_NODES,
        "mitigations": _INPUT_GUARDS | {"mcp-gateway"},
        "severity": "CRITICAL",
        "base_score": 9.0,
        "mitigation_reduction": 5.0,
    },
    {
        "id": "RT-03",
        "tactic": _ATLAS_EVASION,
        "technique": "AML.T0015",
        "title": "Jailbreak / Safety Filter Bypass",
        "description": "Adversarial prompts crafted to bypass safety filters and guardrails.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _INPUT_GUARDS | _OUTPUT_GUARDS,
        "severity": "HIGH",
        "base_score": 8.0,
        "mitigation_reduction": 5.0,
    },
    {
        "id": "RT-04",
        "tactic": _ATLAS_ML_ATTACK,
        "technique": "AML.T0020",
        "title": "Training Data Poisoning",
        "description": "Attacker corrupts training or fine-tuning corpus to alter model behavior.",
        "requires_node_types": {"vector-db", "knowledge-graph"},
        "mitigations": _MONITOR | _AUDIT,
        "severity": "HIGH",
        "base_score": 8.5,
        "mitigation_reduction": 4.5,
    },
    {
        "id": "RT-05",
        "tactic": _ATLAS_RECON,
        "technique": "AML.T0006",
        "title": "Model Extraction",
        "description": "Attacker probes model via repeated queries to extract architecture/weights.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _RATE | _CB,
        "severity": "MEDIUM",
        "base_score": 6.0,
        "mitigation_reduction": 4.0,
    },
    {
        "id": "RT-06",
        "tactic": _ATLAS_RECON,
        "technique": "AML.T0024",
        "title": "Membership Inference Attack",
        "description": "Attacker determines whether specific data samples were used in training.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _MONITOR | _AUDIT,
        "severity": "MEDIUM",
        "base_score": 5.5,
        "mitigation_reduction": 3.5,
    },
    {
        "id": "RT-07",
        "tactic": _ATLAS_EXFIL,
        "technique": "AML.T0056",
        "title": "System Prompt Leakage",
        "description": "Attacker extracts hidden system prompt through adversarial queries.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _PROMPT_REG | _INPUT_GUARDS,
        "severity": "HIGH",
        "base_score": 7.5,
        "mitigation_reduction": 4.5,
    },
    {
        "id": "RT-08",
        "tactic": _ATLAS_IMPACT,
        "technique": "AML.T0048",
        "title": "Goal / Objective Hijacking",
        "description": "Crafted input overrides agent goals, causing unauthorized actions.",
        "requires_node_types": AGENT_NODES,
        "mitigations": _HITL | _INPUT_GUARDS,
        "severity": "CRITICAL",
        "base_score": 9.5,
        "mitigation_reduction": 7.0,
    },
    {
        "id": "RT-09",
        "tactic": _ATLAS_EXFIL,
        "technique": "AML.T0057",
        "title": "PII Exfiltration via RAG",
        "description": "Attacker extracts PII from vector store/knowledge graph via crafted queries.",
        "requires_node_types": {"vector-db", "knowledge-graph"},
        "mitigations": _PII,
        "severity": "HIGH",
        "base_score": 8.0,
        "mitigation_reduction": 6.0,
    },
    {
        "id": "RT-10",
        "tactic": _ATLAS_PERSIST,
        "technique": "AML.T0011",
        "title": "Supply Chain Model Compromise",
        "description": "Backdoored weights or poisoned model checkpoint injected via supply chain.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _AUDIT | _MONITOR,
        "severity": "CRITICAL",
        "base_score": 9.0,
        "mitigation_reduction": 4.0,
    },
    {
        "id": "RT-11",
        "tactic": _ATLAS_EVASION,
        "technique": "AML.T0043",
        "title": "Adversarial Perturbation",
        "description": "Imperceptibly modified inputs cause model misclassification or wrong outputs.",
        "requires_node_types": MODEL_NODES,
        "mitigations": _INPUT_GUARDS | _CB,
        "severity": "MEDIUM",
        "base_score": 6.5,
        "mitigation_reduction": 3.5,
    },
    {
        "id": "RT-12",
        "tactic": _ATLAS_IMPACT,
        "technique": "AML.T0040",
        "title": "Unsandboxed Code Execution",
        "description": "Agent executes attacker-controlled code without sandboxing controls.",
        "requires_node_types": TOOL_MCP_NODES | {"a2a-bridge"},
        "mitigations": _SANDBOX | {"mcp-gateway"},
        "severity": "CRITICAL",
        "base_score": 9.5,
        "mitigation_reduction": 8.0,
    },
]


def run_red_team(nodes: list[dict], edges: list[dict]) -> dict:  # noqa: ARG001
    """
    Evaluate design graph against 12 adversarial attack scenarios.

    Returns dict with keys: scenarios, summary, attack_surface.
    """
    node_types = {n.get("type", "") for n in nodes}

    results = []
    for sc in _SCENARIOS:
        applicable = bool(node_types & sc["requires_node_types"])
        if not applicable:
            results.append({
                **{k: sc[k] for k in ("id", "tactic", "technique", "title", "description", "severity")},
                "applicable": False,
                "exploitability": 0.0,
                "mitigated": True,
                "missing_mitigations": [],
                "affected_node_types": [],
            })
            continue

        affected = sorted(node_types & sc["requires_node_types"])
        mitigated = bool(node_types & sc["mitigations"])
        missing = sorted(sc["mitigations"] - node_types)
        score = sc["base_score"]
        if mitigated:
            score = max(1.0, score - sc["mitigation_reduction"])

        results.append({
            "id": sc["id"],
            "tactic": sc["tactic"],
            "technique": sc["technique"],
            "title": sc["title"],
            "description": sc["description"],
            "severity": sc["severity"],
            "applicable": True,
            "exploitability": round(score, 1),
            "mitigated": mitigated,
            "missing_mitigations": missing[:5],
            "affected_node_types": list(affected),
        })

    applicable = [r for r in results if r["applicable"]]
    critical = sum(1 for r in applicable if r["severity"] == "CRITICAL" and not r["mitigated"])
    high = sum(1 for r in applicable if r["severity"] == "HIGH" and not r["mitigated"])
    unmitigated = sum(1 for r in applicable if not r["mitigated"])
    avg_score = round(sum(r["exploitability"] for r in applicable) / len(applicable), 1) if applicable else 0.0

    # Attack surface: which node categories are exposed
    attack_surface = {
        "llm_exposed": bool(node_types & MODEL_NODES),
        "agents_exposed": bool(node_types & AGENT_NODES),
        "tools_exposed": bool(node_types & TOOL_MCP_NODES),
        "memory_exposed": bool(node_types & {"vector-db", "knowledge-graph"}),
        "unsandboxed_exec": bool(node_types & TOOL_MCP_NODES) and not bool(node_types & _SANDBOX),
        "no_input_guard": bool(node_types & MODEL_NODES) and not bool(node_types & _INPUT_GUARDS),
        "no_output_guard": bool(node_types & MODEL_NODES) and not bool(node_types & _OUTPUT_GUARDS),
        "no_hitl": bool(node_types & AGENT_NODES) and not bool(node_types & _HITL),
        "no_pii_guard": bool(node_types & {"vector-db", "knowledge-graph"}) and not bool(node_types & _PII),
    }

    return {
        "scenarios": results,
        "summary": {
            "total_scenarios": len(_SCENARIOS),
            "applicable": len(applicable),
            "unmitigated": unmitigated,
            "critical_unmitigated": critical,
            "high_unmitigated": high,
            "avg_exploitability": avg_score,
            "overall_risk": (
                "CRITICAL" if critical > 0
                else "HIGH" if high > 0
                else "MEDIUM" if unmitigated > 0
                else "LOW"
            ),
        },
        "attack_surface": attack_surface,
    }
