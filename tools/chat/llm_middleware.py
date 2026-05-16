#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared LLM middleware for every ICDEV™ chat canvas.

Centralises:
  - Prompt + context caching (cache_control="ephemeral", cache_breakpoint)
  - COT/COD routing (analytical canvases use chain_of_thought)
  - Blockchain provenance anchor (non-blocking daemon thread)
  - Ontology enrichment (structured outputs only)
  - Column-level security on DB rows
  - Field-level security on API response bodies
  - Row-level security enforced by get_connection() (transparent)

Usage:
    from tools.chat.llm_middleware import chat_llm_invoke, apply_chat_security

    content, meta = chat_llm_invoke(
        function="chat_response",
        messages=conversation,
        system_prompt=system_prompt,
        canvas_type="strategos",
        session_id=ctx.context_id,
        classification="CUI",
    )
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("icdev.chat.middleware")

# ---------------------------------------------------------------------------
# Canvas-level feature config
# ---------------------------------------------------------------------------

# effort: extended thinking budget (low/medium/high/max)
# cot: use chain-of-thought by default for this canvas
_CANVAS_CONFIG: Dict[str, Dict] = {
    "intake":     {"effort": "medium", "cot": True},
    "cam":        {"effort": "medium", "cot": True},
    "strategos":  {"effort": "high",   "cot": True},
    "simulation": {"effort": "medium", "cot": False},
    "ndc":        {"effort": "low",    "cot": False},
    "sdc":        {"effort": "low",    "cot": False},
    "eda":        {"effort": "low",    "cot": False},
    "ddc":        {"effort": "low",    "cot": False},
    "pdc":        {"effort": "low",    "cot": False},
    "bdc":        {"effort": "low",    "cot": False},
    "odc":        {"effort": "low",    "cot": False},
    "idc":        {"effort": "low",    "cot": False},
    "chat":       {"effort": "low",    "cot": False},
}

_DEFAULT_CONFIG = {"effort": "low", "cot": False}


def _canvas_cfg(canvas_type: str) -> Dict:
    return _CANVAS_CONFIG.get(canvas_type, _DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def chat_llm_invoke(
    function: str,
    messages: List[Dict[str, Any]],
    system_prompt: str = "",
    *,
    canvas_type: str = "chat",
    session_id: str = "",
    classification: str = "CUI",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    project_id: str = "",
    agent_id: str = "chat",
    force_cot: bool = False,
    skip_anchor: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Unified LLM invocation for all chat canvases.

    Returns:
        (content, metadata)  where metadata carries token counts, cache stats,
        blockchain anchor status, and canvas-level config used.
    """
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest

    cfg = _canvas_cfg(canvas_type)
    effort = cfg["effort"]
    use_cot = force_cot or cfg["cot"]

    # Inject cache_breakpoint into system prompt so the stable persona/instructions
    # block is cached and the dynamic session context is not.
    if system_prompt and "<!-- cache_breakpoint -->" not in system_prompt:
        # Split on first double-newline that separates stable from dynamic sections;
        # fall back to appending at end if no natural break exists.
        if "\n\n" in system_prompt:
            first, rest = system_prompt.split("\n\n", 1)
            system_prompt = first + "\n<!-- cache_breakpoint -->\n\n" + rest
        else:
            system_prompt = system_prompt + "\n<!-- cache_breakpoint -->"

    request = LLMRequest(
        messages=messages,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        effort=effort,
        cache_control="ephemeral",
        agent_id=agent_id or f"icdev-{canvas_type}",
        project_id=project_id,
        classification=classification,
    )

    router = LLMRouter()
    response = None

    if use_cot:
        try:
            response = router.invoke_chain_of_thought(function, request)
        except Exception as exc:
            logger.debug("COT unavailable for %s, falling back: %s", canvas_type, exc)
            response = router.invoke(function, request)
    else:
        response = router.invoke(function, request)

    content = (response.content or "").strip() if response else ""

    meta: Dict[str, Any] = {
        "canvas_type": canvas_type,
        "effort": effort,
        "used_cot": use_cot,
        "input_tokens": getattr(response, "input_tokens", 0),
        "output_tokens": getattr(response, "output_tokens", 0),
        "cache_read_tokens": getattr(response, "cache_read_input_tokens", 0),
        "cache_write_tokens": getattr(response, "cache_creation_input_tokens", 0),
        "thinking_tokens": getattr(response, "thinking_tokens", 0),
        "provider": getattr(response, "provider", ""),
        "model_id": getattr(response, "model_id", ""),
        "anchor_status": "skipped",
    }

    # Blockchain provenance — non-blocking
    if not skip_anchor and content and session_id:
        anchor_result = _anchor_async(session_id, content, canvas_type)
        meta["anchor_status"] = anchor_result

    return content, meta


# ---------------------------------------------------------------------------
# Blockchain anchor
# ---------------------------------------------------------------------------

def _anchor_async(session_id: str, content: str, canvas_type: str) -> str:
    """Fire-and-forget blockchain anchor. Returns 'queued' immediately."""
    def _do():
        try:
            from tools.blockchain.chain_anchor import ChainAnchor
            merkle_root = hashlib.sha256(
                f"{session_id}:{canvas_type}:{content[:2000]}".encode()
            ).hexdigest()
            ChainAnchor().anchor_merkle_root(merkle_root, {
                "source": "chat_turn",
                "canvas_type": canvas_type,
                "session_id": session_id,
            })
        except Exception:
            pass

    threading.Thread(target=_do, daemon=True).start()
    return "queued"


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

def apply_chat_security(
    data: Any,
    role: str,
    schema: str = "chat_message",
) -> Any:
    """Apply field-level and column-level security to chat API response data.

    Falls back silently if security modules are unavailable.
    """
    try:
        from tools.security.field_security import apply_field_policy
        return apply_field_policy(schema, role, data)
    except Exception:
        return data


def mask_chat_row(table: str, role: str, row: Dict) -> Dict:
    """Apply column-level masking to a DB row dict."""
    try:
        from tools.security.column_security import apply_column_policy
        return apply_column_policy(table, role, row)
    except Exception:
        return row


def current_role() -> str:
    """Return the current request's role from Flask g, or 'viewer' if outside request."""
    try:
        from flask import g
        ctx = getattr(g, "security_context", None)
        return getattr(ctx, "role", "viewer") if ctx else "viewer"
    except Exception:
        return "viewer"
