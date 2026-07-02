# CUI // SP-CTI
"""Co-Workers canvas (key ``cwk``).

A thin hub over the existing chat-persona registry: it lists the AI
co-workers a user can collaborate with and, on selection, opens the shared
``/chat`` interface seeded with that co-worker's persona and RAG namespace.

No new database table is required — the co-worker roster is config-driven
(``args/chat_personas.yaml`` plus the ACE role catalog), so this canvas reads
existing sources rather than persisting its own rows.
"""
from icdev.tools.coworkers.registry import CoWorker, get_coworker, list_coworkers
from icdev.tools.coworkers.context_factory import build_chat_link, build_persona_seed

__all__ = [
    "CoWorker",
    "list_coworkers",
    "get_coworker",
    "build_chat_link",
    "build_persona_seed",
]
