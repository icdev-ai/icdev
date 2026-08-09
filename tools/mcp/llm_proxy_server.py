#!/usr/bin/env python3

# CUI // SP-CTI
"""LLM Proxy MCP Server — exposes ICDEV™'s LLMRouter as an MCP tool for Goose.

Goose (or any MCP client) calls `llm_invoke` with a function name, messages,
and optional parameters.  This server routes through ICDEV™'s three-tier
LLMRouter (Planner/Worker/Scanner), preserving fallback chains, two-tier
draft+review, RAG injection, redaction, prompt injection scanning, telemetry,
and token budget enforcement.

Usage:
    python tools/mcp/llm_proxy_server.py

Goose config.yaml entry:
    icdev-llm-proxy:
      enabled: true
      type: stdio
      name: icdev-llm-proxy
      cmd: python
      args: [C:/Users/schuo/Downloads/ICDev/tools/mcp/llm_proxy_server.py]
      env:
        ICDEV_PROJECT_ROOT: C:/Users/schuo/Downloads/ICDev
        PYTHONPATH: C:/Users/schuo/Downloads/ICDev
      description: ICDEV™ LLM Router proxy — three-tier routing with fallback chains
      display_name: ICDEV™ LLM Router
      timeout: 120
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.mcp.base_server import MCPServer  # noqa: E402

logger = get_logger("mcp.llm_proxy")

# Lazy-loaded router singleton
_router = None


def _get_router():
    """Lazy-load the LLMRouter singleton."""
    global _router
    if _router is None:
        from tools.llm.router import LLMRouter

        _router = LLMRouter()
        logger.info("LLMRouter initialized")
    return _router


def handle_llm_invoke(args: dict) -> dict:
    """Route an LLM request through ICDEV™'s three-tier router.

    Args (MCP tool input):
        function: ICDEV™ function name (e.g. 'code_generation', 'compliance_export')
        messages: List of {role, content} dicts
        system_prompt: Optional system prompt string
        max_tokens: Optional max tokens (default 4096)
        temperature: Optional temperature (default 1.0)
        effort: Optional effort level (low/medium/high/max)
        agent_id: Optional agent ID for budget tracking

    Returns:
        {content, model_id, provider, input_tokens, output_tokens, duration_ms, status}
    """
    from tools.llm.provider import LLMRequest

    function = args.get("function", "default")
    messages = args.get("messages", [])
    if not messages:
        return {"error": "messages is required", "status": "error"}

    request = LLMRequest(
        messages=messages,
        system_prompt=args.get("system_prompt", ""),
        max_tokens=int(args.get("max_tokens", 4096)),
        temperature=float(args.get("temperature", 1.0)),
        effort=args.get("effort", ""),
        agent_id=args.get("agent_id", "goose"),
        project_id=args.get("project_id", ""),
        classification=args.get("classification", "CUI"),
        skip_injection_scan=bool(args.get("skip_injection_scan", False)),
    )

    router = _get_router()

    try:
        response = router.invoke(function, request)
        return {
            "content": response.content,
            "model_id": response.model_id,
            "provider": response.provider,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "thinking_tokens": response.thinking_tokens,
            "duration_ms": response.duration_ms,
            "stop_reason": response.stop_reason,
            "status": "success",
        }
    except RuntimeError as exc:
        return {"error": str(exc), "status": "error"}
    except Exception as exc:
        logger.error("LLM invoke failed: %s", exc)
        return {"error": str(exc), "status": "error"}


def handle_llm_models(args: dict) -> dict:
    """List available models and their routing configuration."""
    router = _get_router()
    cfg = router._config

    models = {}
    for name, mcfg in cfg.get("models", {}).items():
        models[name] = {
            "model_id": mcfg.get("model_id", ""),
            "provider": mcfg.get("provider", ""),
            "description": mcfg.get("description", ""),
        }

    routing = {}
    for func, rcfg in cfg.get("routing", {}).items():
        routing[func] = {
            "chain": rcfg.get("chain", []),
            "effort": rcfg.get("effort", "medium"),
        }

    two_tier = cfg.get("two_tier", {})

    return {
        "models": models,
        "routing": routing,
        "two_tier_enabled": two_tier.get("enabled", False),
        "two_tier_tier1": two_tier.get("tier1_model", ""),
        "two_tier_tier2": two_tier.get("tier2_model", ""),
        "status": "success",
    }


def handle_llm_availability(args: dict) -> dict:
    """Check which models are currently available."""
    router = _get_router()
    cfg = router._config

    results = {}
    for name in cfg.get("models", {}):
        available = router._check_model_available(name)
        results[name] = available

    available_count = sum(1 for v in results.values() if v)
    return {
        "models": results,
        "available_count": available_count,
        "total_count": len(results),
        "status": "success",
    }


def handle_llm_resolve(args: dict) -> dict:
    """Resolve which provider/model would handle a given function (dry run)."""
    function = args.get("function", "default")
    router = _get_router()

    provider, model_id, model_cfg = router.get_provider_for_function(function)
    if provider is None:
        return {
            "function": function,
            "resolved": False,
            "error": f"No available model for function '{function}'",
            "status": "error",
        }

    return {
        "function": function,
        "resolved": True,
        "model_id": model_id,
        "provider": provider.provider_name,
        "effort": router.get_effort(function),
        "chain": router._get_chain_for_function(function),
        "status": "success",
    }


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------


def create_server() -> MCPServer:
    """Create and configure the LLM proxy MCP server."""
    server = MCPServer(name="icdev-llm-proxy", version="1.0.0")

    server.register_tool(
        name="llm_invoke",
        description=(
            "Invoke ICDEV™'s LLM Router with three-tier routing "
            "(Planner/Worker/Scanner), fallback chains, two-tier draft+review, "
            "RAG injection, PII redaction, and prompt injection scanning. "
            "Supports 11 providers: Ollama, Anthropic, OpenAI, Bedrock, "
            "Azure, Vertex AI, Gemini, OCI, watsonx, vLLM, Mistral."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": (
                        "ICDEV™ function name that determines routing tier and model chain. "
                        "Examples: 'code_generation', 'compliance_export', 'narrative_generation', "
                        "'intake_persona_response', 'memory_consolidation', 'default'"
                    ),
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                    "description": "Chat messages in OpenAI format [{role, content}]",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "Optional system prompt prepended to the conversation",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum response tokens (default 4096)",
                    "default": 4096,
                },
                "temperature": {
                    "type": "number",
                    "description": "Sampling temperature (default 1.0)",
                    "default": 1.0,
                },
                "effort": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "max"],
                    "description": "Effort level — affects model selection and token budget",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID for token budget tracking (default: 'goose')",
                    "default": "goose",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project ID for telemetry and tracing",
                },
            },
            "required": ["function", "messages"],
        },
        handler=handle_llm_invoke,
    )

    server.register_tool(
        name="llm_models",
        description="List all configured LLM models, routing chains, and two-tier settings.",
        input_schema={"type": "object", "properties": {}},
        handler=handle_llm_models,
    )

    server.register_tool(
        name="llm_availability",
        description="Check which LLM models are currently online and reachable.",
        input_schema={"type": "object", "properties": {}},
        handler=handle_llm_availability,
    )

    server.register_tool(
        name="llm_resolve",
        description=(
            "Dry-run: resolve which provider and model would handle a given "
            "ICDEV™ function without actually invoking it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "ICDEV™ function name to resolve",
                },
            },
            "required": ["function"],
        },
        handler=handle_llm_resolve,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
