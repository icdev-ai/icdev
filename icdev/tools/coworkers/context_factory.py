# CUI // SP-CTI
"""Context factory — turns a selected co-worker into a shared-chat entry point.

The ``/coworkers`` hub does not implement its own chat surface; it reuses the
platform's shared ``/chat``.  Given a :class:`CoWorker`, this module builds:

  * a deep link into ``/chat`` with the co-worker's role/goal pre-filled
    (``build_chat_link``), so the conversation opens framed by that persona, and
  * a short persona seed string (``build_persona_seed``) describing who the user
    is talking to and which RAG namespace grounds the conversation.

The shared chat already performs RAG context injection (D-RAG-2); the
``rag_namespace`` is surfaced so a future deep-link parameter or the chat upload
flow can scope retrieval to this co-worker.
"""
from __future__ import annotations

from urllib.parse import urlencode

from icdev.tools.coworkers.registry import CoWorker


def build_persona_seed(coworker: CoWorker) -> str:
    """A one-line framing string for the opening of a co-worker chat."""
    return (
        f"You are collaborating with {coworker.name} ({coworker.role}). "
        f"{coworker.description}".strip()
    )


def build_chat_link(coworker: CoWorker) -> str:
    """Return a relative ``/chat`` URL seeded for this co-worker.

    ``role`` pre-fills the persona framing the chat already honors; ``goal``
    seeds the opening intent.  Both are query params understood by the shared
    ``/chat`` route — no chat-side changes are required.
    """
    params = {
        "role": coworker.role,
        "goal": f"Collaborate with {coworker.name}",
        "coworker": coworker.id,
    }
    # Forward RAG grounding scopes when available (ref co-workers carry them).
    if coworker.rag_tables:
        params["rag_tables"] = ",".join(coworker.rag_tables)
    if coworker.goals:
        params["goals"] = ",".join(coworker.goals)
    if coworker.manifest_shards:
        params["manifest_shards"] = ",".join(coworker.manifest_shards)
    if coworker.trust_tier:
        params["trust_tier"] = coworker.trust_tier
    return "/chat?" + urlencode(params)
