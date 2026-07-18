# CUI // SP-CTI
"""Shared RAG retrieval helpers used by more than one call site.

Two call sites construct a tenant-scoped ``RAGRetriever`` and run its
two-stage search:

- ``tools/cortex/search_service.py::search_rag`` — the Cortex ``rag`` backend
  adapter. It clamps each ``final_score`` into ``[0, 1]`` for the unified
  ``CortexSearchResult`` shape.
- ``tools/mcp/rag_server.py::handle_rag_search`` — the ``rag_search`` MCP tool
  handler.

The genuinely-common logic is (a) constructing the retriever with a
``tenant_id`` (tenant scoping) and invoking ``.search()``, and (b) the
unit-interval score clamp. This module owns both so the two sites do not
re-derive them independently.

The retriever CLASS is passed in by the caller rather than imported here, so
the namespace-consistent / monkeypatch-aware resolution each caller already
performs (``tools.*`` shim vs canonical ``icdev.tools.*``) stays under the
caller's control — see the ``tools/cortex/search_service.py`` module
docstring for why that resolution matters.

Behavior note (divergence, intentionally preserved): ``handle_rag_search``
forwards ``filters=`` and ``agent_id=`` keyword arguments that
``RAGRetriever.search()`` does not accept. This helper forwards
``search_kwargs`` verbatim, so that call still raises ``TypeError`` exactly as
before (the handler catches it and returns an error). Only ``search_rag``
consumes ``clamp_unit`` — the MCP handler normalizes via
``SearchResult.to_dict()`` rounding instead. Both facts are documented rather
than silently unified.
"""
from __future__ import annotations

from typing import Any, List


def clamp_unit(value: Any) -> float:
    """Coerce ``value`` to ``float`` and clamp to ``[0, 1]``.

    Unparseable values (``None``, non-numeric strings) become ``0.0``. This is
    the unit-interval normalization the Cortex ``rag`` adapter applies to each
    ``final_score``; centralized here so score normalization lives in one place.
    """
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def run_rag_search(
    retriever_cls: Any,
    query: str,
    *,
    tenant_id: str = "",
    **search_kwargs: Any,
) -> List[Any]:
    """Construct a tenant-scoped retriever and run its two-stage search.

    ``retriever_cls`` is the ``RAGRetriever`` class resolved by the caller
    (passed in, not imported, to keep namespace resolution and test
    monkeypatching under the caller's control). ``tenant_id`` scopes the
    vector-store filters; ``search_kwargs`` are forwarded verbatim to
    ``retriever.search()`` (e.g. ``top_k``, ``source_types``) so each caller
    keeps its own argument set.

    Returns the retriever's result list, or ``[]`` when it yields nothing.
    Exceptions propagate: every current caller wraps this in its own
    ``try/except`` and degrades to an empty/​error result.
    """
    retriever = retriever_cls(tenant_id=tenant_id)
    return retriever.search(query, **search_kwargs) or []
