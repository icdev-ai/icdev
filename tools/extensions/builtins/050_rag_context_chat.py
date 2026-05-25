#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""RAG source attribution chat extension (D-RAG-2, D-RAG-8).

Hooks into ``chat_message_after`` to surface RAG knowledge sources used
to generate the assistant response.  When the chat_manager injects RAG
context into the system prompt, the retrieved sources are passed through
to this extension via the ``rag_sources`` key in the hook context.

This extension formats them into a user-friendly attribution message so
the user knows which knowledge sources informed the response.

Advisory messages are throttled: only shown when sources are present.

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""


logger = get_logger("icdev.extensions.rag_context_chat")


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject RAG source attribution."""
    # Only assistant responses
    if context.get("role") != "assistant":
        return context

    rag_sources = context.get("rag_sources", [])
    if not rag_sources:
        return context

    # Format sources for display
    source_lines = []
    for i, src in enumerate(rag_sources, 1):
        source_type = src.get("source_type", "unknown")
        score = src.get("score", 0)
        preview = src.get("content", "")[:120].replace("\n", " ")
        source_lines.append(f"  [{i}] {source_type} (relevance: {score:.0%}): {preview}...")

    sources_text = "\n".join(source_lines)

    result = dict(context)
    result["rag_advisory"] = {
        "gap_id": "rag_sources_used",
        "severity": "info",
        "message": (f"This response was enriched with {len(rag_sources)} knowledge source(s):\n{sources_text}"),
        "action": "",
        "source_count": len(rag_sources),
    }
    return result


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

NAME = "rag_context_chat"
PRIORITY = 50
ALLOW_MODIFICATION = True
DESCRIPTION = "Surface RAG knowledge sources used in response generation (D-RAG-2, D-RAG-8)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
