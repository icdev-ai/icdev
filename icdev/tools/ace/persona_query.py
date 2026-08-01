# CUI // SP-CTI
"""Synchronous single-persona query.

Deliberately bypasses `ACEController.launch()`'s async multi-role
team-launch machinery -- that path inserts a `pending` `ace_instances` row
and hands off to a background thread for a full collaborative team run,
which is the wrong shape for "ask one expert one question, get one text
answer back." This module is the one-shot equivalent: it loads a single
role's SOUL.md-derived identity via `tools.ace.soul_manager.
build_identity_preamble`, makes ONE LLM call framed with that identity, and
returns the answer directly.

Primary caller today is the `ace_persona_query` MCP tool
(`tools/mcp/gap_handlers.py::handle_ace_persona_query`), which exposes this
to cross-repo callers (e.g. idea_lab) that have no other way to consult an
ACE persona synchronously.
"""
from __future__ import annotations

from icdev.tools.ace.soul_manager import build_identity_preamble
from icdev.tools.llm import get_router
from icdev.tools.llm.provider import LLMRequest
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_UNKNOWN_ROLE_FALLBACK = (
    "You are a generalist technical advisor at ICDEV. No specific persona "
    "identity file was found for the requested role; answer from general "
    "software/IT expertise."
)


def query_persona(role_id: str, question: str, context: str = "") -> str:
    """One synchronous LLM call informed by `role_id`'s SOUL.md identity.

    Returns the persona's answer as plain text.

    Raises:
        ValueError: if `role_id` or `question` is empty.
        LLMUnavailableError: if no provider in the configured chain can
            serve the request (see `tools.llm.router.LLMRouter.invoke`).
    """
    role_id = (role_id or "").strip()
    question = (question or "").strip()
    if not role_id or not question:
        raise ValueError("role_id and question are required")

    identity = build_identity_preamble(role_id) or _UNKNOWN_ROLE_FALLBACK
    user_content = f"Context:\n{context}\n\nQuestion:\n{question}" if context else question

    router = get_router()
    response = router.invoke(
        "ace_persona_query",
        LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=identity,
            max_tokens=1024,
            temperature=0.5,
            effort="medium",
        ),
    )
    return (response.content or "").strip()
