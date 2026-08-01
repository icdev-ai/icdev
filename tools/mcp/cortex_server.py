#!/usr/bin/env python3
# CUI // SP-CTI
"""Cortex MCP server — 8 tools exposing the unified ICDEV Cortex facade.

The Cortex facade (``tools/cortex/api.py``) is the platform's unified AI layer
over LLMRouter / RAG / KG / DIC / IQE / ACE with a single enforced TRUST
(governance) pipeline. This server is the *programmable API surface* of that
pattern: it lets agents, skills, and external MCP clients consume the unified
layer instead of the fragmented per-subsystem tools.

Each handler is a thin one-liner into ``tools.cortex``:

    cortex_search        -> cortex.search()            -> [CortexSearchResult]
    cortex_ask           -> cortex.ask()               -> CortexResult
    cortex_complete      -> cortex.complete()          -> CortexResult
    cortex_reason        -> cortex.reason()            -> CortexResult
    cortex_classify      -> cortex.classify()          -> CortexResult
    cortex_extract       -> cortex.extract()           -> CortexResult
    cortex_govern        -> cortex.govern()            -> GovernanceReport
    cortex_agent_launch  -> cortex.agent()             -> CortexResult

``cortex.govern`` / ``cortex.agent`` land with the governance epic
(ctx-govern-04). Until that facade is present this server falls back to the
same seams the facade uses — the standalone ``GovernancePipeline`` for govern,
and ``ACEController`` / ``run_agent_loop`` for agent — so every tool is
functional today and transparently prefers the ``api`` facade once it merges.

Every governed facade may raise ``GovernanceBlockedError`` (pre-check block /
fail-closed gate). Handlers catch it and return the ``GovernanceReport`` so the
block is observable in the tool response rather than a bare stack trace.

Security note: the unified MCP gateway (``tools/gateway/security_chain.py`` +
``base_server`` D284 instrumentation) wraps all gateway traffic automatically —
this server adds NO transport-layer auth of its own by design.

Usage:
    python tools/mcp/cortex_server.py          # Start MCP server (stdio)
    python tools/mcp/cortex_server.py --help   # Show help
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.mcp.base_server import MCPServer  # noqa: E402

_CLASSIFICATION = "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _build_context(arguments: Dict[str, Any]):
    """Map the optional MCP identity/policy params onto a CortexContext.

    tenant_id / classification / domain are exposed on every tool so RLS
    predicates and read-down filtering key off the context; user_id is
    threaded too (cost attribution on the agent path).
    """
    from tools.cortex import CortexContext

    return CortexContext(
        tenant_id=arguments.get("tenant_id") or "",
        user_id=arguments.get("user_id") or "",
        classification=arguments.get("classification") or "CUI",
        domain=arguments.get("domain") or "",
        fail_closed=bool(arguments.get("fail_closed", False)),
    )


def _blocked_response(exc) -> Dict[str, Any]:
    """Serialize a GovernanceBlockedError into a stable tool response."""
    report = getattr(exc, "report", None)
    return {
        "classification": _CLASSIFICATION,
        "error": str(exc),
        "blocked": True,
        "blocked_gate": getattr(exc, "gate", ""),
        "governance": report.to_dict() if hasattr(report, "to_dict") else {},
    }


# ---------------------------------------------------------------------------
# Handlers — one thin call into tools.cortex per tool
# ---------------------------------------------------------------------------
def handle_cortex_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Unified Cortex search across rag / graph / dic / kb with strategy routing."""
    query = arguments.get("query", "")
    if not query:
        return {"error": "query is required", "results": []}

    top_k = int(arguments.get("top_k", 5))
    strategy = arguments.get("strategy") or "auto"

    try:
        from tools.cortex import search

        results = search(query, top_k=top_k, ctx=_build_context(arguments), strategy=strategy)
        return {
            "classification": _CLASSIFICATION,
            "query": query,
            "strategy": strategy,
            "results_count": len(results),
            "results": [r.to_dict() for r in results],
        }
    except ImportError:
        return {"error": "Cortex search subsystem not available", "results": []}
    except Exception as exc:
        return {"error": str(exc), "results": []}


def handle_cortex_ask(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a natural-language data question (IQE primary, NL->SQL fallback)."""
    question = arguments.get("question", "")
    if not question:
        return {"error": "question is required"}

    try:
        from tools.cortex import ask
        from tools.cortex.analyst import CortexAnalystError, CortexQueryBlocked

        try:
            result = ask(
                question,
                mode=arguments.get("mode") or "auto",
                ctx=_build_context(arguments),
                canvas=arguments.get("canvas") or None,
                collections=arguments.get("collections") or None,
                summarize=bool(arguments.get("summarize", False)),
            )
            return {"classification": _CLASSIFICATION, **result.to_dict()}
        except CortexQueryBlocked as exc:
            return {
                "classification": _CLASSIFICATION,
                "error": str(exc),
                "blocked": True,
                "governance": exc.governance.to_dict()
                if hasattr(getattr(exc, "governance", None), "to_dict")
                else {},
            }
        except CortexAnalystError as exc:
            return {"error": str(exc)}
    except ImportError:
        return {"error": "Cortex analyst subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def handle_cortex_complete(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Free-form completion via the config-routed LLM chain, governed end to end."""
    prompt = arguments.get("prompt", "")
    if not prompt:
        return {"error": "prompt is required"}

    try:
        from tools.cortex import complete
        from tools.cortex.governance import GovernanceBlockedError

        try:
            result = complete(
                prompt,
                ctx=_build_context(arguments),
                system_prompt=arguments.get("system_prompt") or "",
                max_tokens=arguments.get("max_tokens"),
                temperature=arguments.get("temperature"),
            )
            return {"classification": _CLASSIFICATION, **result.to_dict()}
        except GovernanceBlockedError as exc:
            return _blocked_response(exc)
    except ImportError:
        return {"error": "Cortex complete subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def handle_cortex_reason(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Multi-step reasoning (cot | debate | council) via the governed Cortex chain."""
    prompt = arguments.get("prompt", "")
    if not prompt:
        return {"error": "prompt is required"}

    try:
        from tools.cortex import reason
        from tools.cortex.governance import GovernanceBlockedError

        try:
            result = reason(
                prompt,
                mode=arguments.get("mode") or "cot",
                ctx=_build_context(arguments),
                system_prompt=arguments.get("system_prompt") or "",
                max_tokens=arguments.get("max_tokens"),
                temperature=arguments.get("temperature"),
            )
            return {"classification": _CLASSIFICATION, **result.to_dict()}
        except GovernanceBlockedError as exc:
            return _blocked_response(exc)
    except ImportError:
        return {"error": "Cortex reason subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def handle_cortex_classify(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Classify text into exactly one of the supplied labels (LLM + heuristic fallback)."""
    text = arguments.get("text", "")
    labels = arguments.get("labels") or []
    if not text:
        return {"error": "text is required"}
    if not labels:
        return {"error": "labels (non-empty list) is required"}

    try:
        from tools.cortex import classify
        from tools.cortex.governance import GovernanceBlockedError

        try:
            result = classify(text, list(labels), ctx=_build_context(arguments))
            return {"classification": _CLASSIFICATION, **result.to_dict()}
        except GovernanceBlockedError as exc:
            return _blocked_response(exc)
    except ImportError:
        return {"error": "Cortex classify subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def handle_cortex_extract(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Extract structured data from text conforming to a JSON schema."""
    text = arguments.get("text", "")
    schema = arguments.get("schema") or {}
    if not text:
        return {"error": "text is required"}
    if not schema:
        return {"error": "schema (JSON schema object) is required"}

    try:
        from tools.cortex import extract
        from tools.cortex.governance import GovernanceBlockedError

        try:
            result = extract(text, schema, ctx=_build_context(arguments))
            return {"classification": _CLASSIFICATION, **result.to_dict()}
        except GovernanceBlockedError as exc:
            return _blocked_response(exc)
    except ImportError:
        return {"error": "Cortex extract subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def handle_cortex_govern(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run the enforced TRUST chain standalone over already-produced text.

    Prefers the ``cortex.govern`` facade (ctx-govern-04); falls back to the
    standalone ``GovernancePipeline`` (identity operation) so external callers
    can adopt the TRUST chain today.
    """
    text = arguments.get("text", "")
    if not text:
        return {"error": "text is required"}
    sources = arguments.get("sources")  # int count | list of ids/dicts

    try:
        import tools.cortex.api as cortex_api
        from tools.cortex.governance import GovernanceBlockedError

        ctx = _build_context(arguments)
        try:
            govern_fn = getattr(cortex_api, "govern", None)
            if callable(govern_fn):
                report = govern_fn(text, sources=sources, ctx=ctx)
            else:
                # Fallback: run the pipeline directly over an identity op.
                from tools.cortex.governance import GovernancePipeline

                _, report = GovernancePipeline(operation="cortex.govern").wrap(
                    lambda governed: governed,
                    ctx,
                    prompt=text,
                    context_sources=sources,
                    retrieval=True,
                )
            return {
                "classification": _CLASSIFICATION,
                "governance": report.to_dict() if hasattr(report, "to_dict") else report,
            }
        except GovernanceBlockedError as exc:
            return _blocked_response(exc)
    except ImportError:
        return {"error": "Cortex governance subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def handle_cortex_agent_launch(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run a goal through the multi-agent stack (ACE team or single agent-loop), governed.

    Prefers the ``cortex.agent`` facade (ctx-govern-04); falls back to the same
    seams the facade uses (``ACEController`` for team launches, ``run_agent_loop``
    for single-agent goals) so the tool launches work today.
    """
    goal = arguments.get("goal", "")
    if not goal:
        return {"error": "goal is required"}

    roles = arguments.get("roles") or None
    mode = arguments.get("mode") or "auto"

    try:
        import tools.cortex.api as cortex_api
        from tools.cortex.governance import GovernanceBlockedError

        ctx = _build_context(arguments)
        try:
            agent_fn = getattr(cortex_api, "agent", None)
            if callable(agent_fn):
                result = agent_fn(
                    goal,
                    roles=roles,
                    ctx=ctx,
                    mode=mode,
                    trigger_source=arguments.get("trigger_source") or "cortex.agent",
                    trigger_ref=arguments.get("trigger_ref") or "",
                    webhook_url=arguments.get("webhook_url") or "",
                    llm_function=arguments.get("llm_function") or "code_generation",
                    max_iterations=int(arguments.get("max_iterations", 12)),
                )
            else:
                result = _agent_launch_fallback(goal, roles, mode, ctx, arguments)
            return {"classification": _CLASSIFICATION, **result.to_dict()}
        except GovernanceBlockedError as exc:
            return _blocked_response(exc)
    except ImportError:
        return {"error": "Cortex agent subsystem not available"}
    except Exception as exc:
        return {"error": str(exc)}


def _agent_launch_fallback(goal: str, roles: Optional[list], mode: str, ctx, arguments: Dict[str, Any]):
    """Ungoverned functional fallback until the cortex.agent facade lands.

    Mirrors ``cortex.agent`` (ctx-govern-04): team launch via ACEController,
    single-agent goal via run_agent_loop. Returns a CortexResult.
    """
    from tools.cortex import CortexResult

    use_team = mode == "team" or (mode == "auto" and bool(roles))

    if use_team:
        from tools.ace.controller import ACEController

        controller = ACEController.get_instance()
        instance_id = controller.launch(
            problem_text=goal,
            trigger_source=arguments.get("trigger_source") or "cortex.agent",
            trigger_ref=arguments.get("trigger_ref") or (ctx.tenant_id or "cortex"),
            user_id=ctx.user_id or "system",
            project_id=ctx.tenant_id or "",
            webhook_url=arguments.get("webhook_url") or "",
            role_ids=list(roles) if roles else None,
        )
        return CortexResult(
            text=f"Launched ACE team run {instance_id} for goal: {goal}",
            provider="ace",
            data={
                "mode": "team",
                "instance_id": instance_id,
                "roles": list(roles) if roles else [],
                "goal": goal,
            },
        )

    from tools.llm import get_router
    from tools.llm.agent_loop import run_agent_loop

    loop = run_agent_loop(
        get_router(),
        system_prompt=arguments.get("system_prompt")
        or "You are an ICDEV autonomous agent. Complete the goal.",
        user_prompt=goal,
        tools=arguments.get("tools") or [],
        tool_handlers={},
        llm_function=arguments.get("llm_function") or "code_generation",
        max_iterations=int(arguments.get("max_iterations", 12)),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )
    return CortexResult(
        text=loop.final_content or "",
        provider=loop.provider or "agent_loop",
        model=loop.model_id or "",
        cost=float(loop.total_cost_usd or 0.0),
        data={
            "mode": "single",
            "result_subtype": loop.result_subtype,
            "turns": loop.turns,
            "done": loop.done,
            "truncated": loop.truncated,
            "session_id": loop.session_id,
            "tool_call_log": loop.tool_call_log,
        },
    )


# ---------------------------------------------------------------------------
# Input schemas — shared context params + per-tool params
# ---------------------------------------------------------------------------
# Optional CortexContext params exposed on every Cortex tool.
_CONTEXT_PROPERTIES = {
    "tenant_id": {"type": "string", "description": "Tenant ID for multi-tenant RLS isolation"},
    "classification": {
        "type": "string",
        "description": "Data classification (CUI, CUI//SP-CTI, SECRET); default CUI",
    },
    "domain": {"type": "string", "description": "Optional domain scope (intersects backend selection)"},
    "user_id": {"type": "string", "description": "Caller user ID (cost attribution / RLS)"},
    "fail_closed": {
        "type": "boolean",
        "description": "Block the response on any governance gate failure instead of degrading",
        "default": False,
    },
}


def _schema(properties: Dict[str, Any], required: list) -> Dict[str, Any]:
    """Compose a tool input schema from tool-specific props + shared context props."""
    return {
        "type": "object",
        "properties": {**properties, **_CONTEXT_PROPERTIES},
        "required": required,
    }


# ---------------------------------------------------------------------------
# MCPServer — register all 8 Cortex tools
# ---------------------------------------------------------------------------
CORTEX_TOOLS = (
    {
        "name": "cortex_search",
        "handler": handle_cortex_search,
        "description": (
            "Unified Cortex search across all retrieval backends (rag / graph / dic / kb) with "
            "agentic strategy routing + CRAG corrective loop. Returns normalized CortexSearchResults "
            "(score 0-1, per-result citation + routing metadata)."
        ),
        "properties": {
            "query": {"type": "string", "description": "Natural language search query"},
            "top_k": {"type": "integer", "description": "Results per backend (default 5)", "default": 5},
            "strategy": {
                "type": "string",
                "description": "auto | rag | graph | dic | kb | all (auto classifies + routes)",
                "default": "auto",
            },
        },
        "required": ["query"],
    },
    {
        "name": "cortex_ask",
        "handler": handle_cortex_ask,
        "description": (
            "Answer a natural-language data question through the Cortex Analyst (IQE primary, "
            "NL->SQL fallback) with a shared injection/SELECT-only safety screen. Returns a "
            "CortexResult with row data, executed IQE/SQL, analyst citations, and TRUST labels."
        ),
        "properties": {
            "question": {"type": "string", "description": "Free-form data question"},
            "mode": {"type": "string", "description": "auto | iqe | nlq", "default": "auto"},
            "canvas": {"type": "string", "description": "Restrict to one canvas's collections"},
            "collections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit collection names (bypasses resolution)",
            },
            "summarize": {
                "type": "boolean",
                "description": "Replace deterministic row summary with cited LLM prose",
                "default": False,
            },
        },
        "required": ["question"],
    },
    {
        "name": "cortex_complete",
        "handler": handle_cortex_complete,
        "description": (
            "Free-form LLM completion via the config-routed Cortex chain, governed end to end "
            "(injection screen, input/output redaction, provenance, audit). Returns a CortexResult "
            "with provider/model/cost/latency accounting and the GovernanceReport."
        ),
        "properties": {
            "prompt": {"type": "string", "description": "User prompt text"},
            "system_prompt": {"type": "string", "description": "Optional system prompt"},
            "max_tokens": {"type": "integer", "description": "Max output tokens (optional)"},
            "temperature": {"type": "number", "description": "Sampling temperature (optional)"},
        },
        "required": ["prompt"],
    },
    {
        "name": "cortex_reason",
        "handler": handle_cortex_reason,
        "description": (
            "Multi-step reasoning via the router's chain orchestration, governed end to end. "
            "mode selects the strategy: 'cot' (chain of thought), 'debate' (proposer/critic rounds), "
            "'council' (fixed-perspective advisors + chairman synthesis). Returns a CortexResult with "
            "provider/model/cost/latency accounting, metadata.reason_mode, and the GovernanceReport."
        ),
        "properties": {
            "prompt": {"type": "string", "description": "Reasoning prompt text"},
            "mode": {
                "type": "string",
                "description": "Reasoning strategy: cot | debate | council (default cot)",
                "default": "cot",
            },
            "system_prompt": {"type": "string", "description": "Optional system prompt"},
            "max_tokens": {"type": "integer", "description": "Max output tokens (optional)"},
            "temperature": {"type": "number", "description": "Sampling temperature (optional)"},
        },
        "required": ["prompt"],
    },
    {
        "name": "cortex_classify",
        "handler": handle_cortex_classify,
        "description": (
            "Classify text into exactly one of the supplied labels via the Cortex chain, degrading "
            "to deterministic query-classifier heuristics when the router is unavailable. Returns a "
            "CortexResult whose text is the chosen label."
        ),
        "properties": {
            "text": {"type": "string", "description": "Text to classify"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Candidate labels (exactly one is chosen)",
            },
        },
        "required": ["text", "labels"],
    },
    {
        "name": "cortex_extract",
        "handler": handle_cortex_extract,
        "description": (
            "Extract structured data from text as a single JSON object conforming to the supplied "
            "JSON schema (native structured output when the provider supports it, fenced-JSON parse "
            "otherwise). Returns a CortexResult whose text is the extracted JSON."
        ),
        "properties": {
            "text": {"type": "string", "description": "Source text to extract from"},
            "schema": {"type": "object", "description": "JSON schema the output must conform to"},
        },
        "required": ["text", "schema"],
    },
    {
        "name": "cortex_govern",
        "handler": handle_cortex_govern,
        "description": (
            "Run the enforced Cortex TRUST chain standalone over already-produced text: gateway "
            "pre-check, input/output redaction, citation + content grounding against the supplied "
            "sources, provenance, and audit. Returns the GovernanceReport."
        ),
        "properties": {
            "text": {"type": "string", "description": "Drafted text to govern"},
            "sources": {
                "type": ["integer", "array"],
                "description": "Grounding sources: an int source count, or a list of id strings/dicts",
                "items": {"type": ["string", "object"]},
            },
        },
        "required": ["text"],
    },
    {
        "name": "cortex_agent_launch",
        "handler": handle_cortex_agent_launch,
        "description": (
            "Run a goal through the multi-agent stack, governed end to end. mode='team' (or 'auto' "
            "with roles) launches a non-blocking ACE run and returns its instance_id; mode='single' "
            "(or 'auto' without roles) runs a single agent-loop and returns its final content. "
            "Returns a CortexResult."
        ),
        "properties": {
            "goal": {"type": "string", "description": "The goal / problem statement for the agent(s)"},
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ACE role IDs for a team launch (implies mode=team under auto)",
            },
            "mode": {"type": "string", "description": "auto | team | single", "default": "auto"},
            "trigger_source": {"type": "string", "description": "Audit trigger source label"},
            "trigger_ref": {"type": "string", "description": "Audit trigger reference"},
            "webhook_url": {"type": "string", "description": "Optional completion webhook (team mode)"},
            "llm_function": {
                "type": "string",
                "description": "Routing function for the single-agent loop (default code_generation)",
                "default": "code_generation",
            },
            "max_iterations": {
                "type": "integer",
                "description": "Max single-agent loop iterations (default 12)",
                "default": 12,
            },
        },
        "required": ["goal"],
    },
)


def build_server() -> MCPServer:
    """Build and return a configured Cortex MCP server (8 tools)."""
    server = MCPServer(name="icdev-cortex", version="1.0.0")
    for tool in CORTEX_TOOLS:
        server.register_tool(
            name=tool["name"],
            description=tool["description"],
            input_schema=_schema(tool["properties"], tool["required"]),
            handler=tool["handler"],
        )
    return server


# ---------------------------------------------------------------------------
# CLI / entry point
# ---------------------------------------------------------------------------
def main():
    """Build and run the Cortex MCP server over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
