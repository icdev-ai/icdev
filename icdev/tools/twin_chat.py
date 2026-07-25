
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""LLM-powered natural language → twin delta converter for all 5 design canvases."""

import json
from typing import Optional

logger = get_logger("icdev.twin_chat")

_NETWORK_SYSTEM = """You are a network topology change analyst for a classified government network.
The user will describe a proposed change in plain English. Convert it to a JSON topology delta.

Output ONLY a valid JSON object in this exact format:
{
  "add_devices": [{"id": "...", "label": "...", "type": "...", "ip": "...", "subnet": "..."}],
  "remove_devices": ["device-id"],
  "add_links": [{"source": "node-id", "target": "node-id", "protocol": "...", "bandwidth_mbps": 1000}],
  "remove_links": ["link-id"],
  "acl_changes": [{"device": "...", "rule": "...", "action": "allow", "port": "443", "protocol": "tcp"}]
}
Only include arrays for what the user is changing. Use [] for unchanged categories.
Valid device types: router, switch-l2, switch-l3, firewall, server, load-balancer, vpn-gateway, cloud-endpoint.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the change."""

_SECURITY_SYSTEM = """You are a security architecture analyst for a classified DoD system.
The user will describe a change to the attack surface or security controls in plain English.
Convert it to a JSON topology delta using STRIDE threat modeling.

Output ONLY a valid JSON object:
{
  "nodes": [{"id": "...", "type": "...", "label": "...", "threat_level": "low|medium|high|critical"}],
  "edges": [{"source": "node-id", "target": "node-id", "label": "...", "authenticated": true}]
}
Only include nodes/edges being added or changed. Use [] for no changes.
Valid node types: asset-server, asset-db, asset-user, trust-boundary, control-firewall, control-waf, control-iam, external-threat.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the security impact."""

_BOUNDARY_SYSTEM = """You are a compliance control analyst for FedRAMP/CMMC/NIST RMF.
The user will describe a compliance or policy change in plain English.
Convert it to a JSON array of control status changes.

Output ONLY a valid JSON array:
[
  {"control_id": "AC-2", "implementation_status": "satisfied|not_satisfied|partial|not_applicable", "evidence_ref": "evidence/filename.pdf"}
]
Map the user's description to NIST 800-53 control IDs. Include 'evidence_ref' only when the user mentions evidence.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the compliance impact."""

_DATA_SYSTEM = """You are a data schema change analyst for a classified data pipeline.
The user will describe a schema or lineage change in plain English.
Convert it to a JSON array of schema changes.

Output ONLY a valid JSON array:
[
  {"table": "table_name", "change": "add_column|remove_column|rename_column|change_type|add_table|remove_table",
   "column": "col_name", "type": "TEXT|INTEGER|TIMESTAMP|BOOLEAN|NUMERIC", "nullable": true, "old_name": "old"}
]
Include 'old_name' only for rename_column. Include 'column'/'type'/'nullable' only for add/change operations.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the schema impact."""

_OBSERVABILITY_SYSTEM = """You are an OpenTelemetry instrumentation analyst.
The user will describe a change to the OTel collector topology in plain English.
Convert it to a JSON collector delta object.

Output ONLY a valid JSON object:
{
  "add_receivers": ["prometheus/service-name"],
  "remove_receivers": ["jaeger/old-service"],
  "add_exporters": ["otlp/new-backend"],
  "remove_exporters": ["zipkin/legacy"],
  "services": {"new-service": {"traces": true, "metrics": true, "logs": false}}
}
Only include fields relevant to the requested change. Omit unchanged arrays.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the instrumentation impact."""


def _call_llm(system_prompt: str, user_message: str, context_json: Optional[str] = None) -> dict:
    """Call LLM router and parse JSON delta + explanation from the response."""
    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMUnavailableError
    except ImportError as e:
        return {"error": f"LLM module unavailable: {e}"}

    try:
        router = get_router()
        if not router.has_any_llm():
            return {"error": "No LLM provider configured — check args/llm_config.yaml or set OLLAMA_BASE_URL."}
    except Exception as e:
        return {"error": f"LLM router init failed: {e}"}

    user_content = user_message
    if context_json:
        user_content = f"Current topology context:\n{context_json}\n\nRequested change: {user_message}"

    try:
        req = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system_prompt,
            max_tokens=2048,
            temperature=0.1,
            skip_injection_scan=False,
            classification="CUI",
        )
        response = router.invoke("twin_chat_delta", req)
        content = (response.content or "").strip()
    except LLMUnavailableError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error("LLM invocation error in twin_chat: %s", e)
        return {"error": f"LLM error: {e}"}

    explanation = ""
    delta_text = content
    if "\nEXPLANATION:" in content:
        parts = content.split("\nEXPLANATION:", 1)
        delta_text = parts[0].strip()
        explanation = parts[1].strip()

    # Strip markdown fences
    if delta_text.startswith("```"):
        lines = delta_text.split("\n")
        delta_text = "\n".join(ln for ln in lines if not ln.startswith("```")).strip()

    try:
        delta = json.loads(delta_text)
        return {"delta": delta, "explanation": explanation}
    except json.JSONDecodeError as e:
        return {"error": f"LLM returned invalid JSON: {e}", "raw": delta_text[:500]}


def network_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_NETWORK_SYSTEM, message, ctx)


def security_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_SECURITY_SYSTEM, message, ctx)


def boundary_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_BOUNDARY_SYSTEM, message, ctx)


def data_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_DATA_SYSTEM, message, ctx)


def observability_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_OBSERVABILITY_SYSTEM, message, ctx)


_QUALITY_SYSTEM = """You are a software-quality gate topology analyst.
The user will describe a change to a Quality Design Canvas gate/coverage graph in plain English.
Convert it to a JSON graph delta describing the proposed topology.

Output ONLY a valid JSON object:
{
  "nodes": [{"id": "gate-1", "type": "quality-gate", "label": "Coverage Gate"}],
  "edges": [{"source": "gate-1", "target": "deploy"}]
}
Include the FULL proposed node/edge set (the twin diffs it against the baseline to
detect removed quality gates). Removing a quality-gate node lowers assurance.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the quality impact."""


_AGENTIC_SYSTEM = """You are an agentic-AI reliability analyst.
The user will describe an agent-failure scenario over an Agentic AI Canvas topology in plain English.
Convert it to a JSON delta naming the failing agents.

Output ONLY a valid JSON object:
{
  "fail_nodes": ["agent-1"],
  "remove_nodes": []
}
Use node ids from the provided topology context when available. The twin propagates
the failure downstream along the agent dependency edges and weights the verdict by
safety/rights-impacting flags.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the cascade impact."""


def quality_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_QUALITY_SYSTEM, message, ctx)


def agentic_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_AGENTIC_SYSTEM, message, ctx)


_AIML_SYSTEM = """You are an AI/ML system architecture analyst.
The user will describe a change to an AI/ML Design Canvas architecture graph in plain English.
Convert it to a JSON graph delta describing the proposed architecture.

Output ONLY a valid JSON object:
{
  "nodes": [{"id": "model-1", "type": "model", "label": "LLM"}],
  "edges": [{"source": "model-1", "target": "guardrail-1"}]
}
Include the FULL proposed node/edge set (the twin diffs it against the baseline to
detect removed governance/architecture nodes). Removing a node may drop a governance control.
Do NOT include markdown fences or any text before the JSON.
After the JSON, on a new line write "EXPLANATION:" followed by one sentence summarizing the AI-governance impact."""


def aiml_chat_to_delta(message: str, graph_json: Optional[dict] = None) -> dict:
    ctx = json.dumps(graph_json, indent=2) if graph_json else None
    return _call_llm(_AIML_SYSTEM, message, ctx)
