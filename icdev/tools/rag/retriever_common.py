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

Behavior note: both call sites forward only kwargs that
``RAGRetriever.search()`` actually accepts (``top_k``, ``source_types``,
``project_id``, ``rerank``, ``query_label``); ``search_kwargs`` is forwarded
verbatim, so passing an unsupported kwarg would raise ``TypeError`` — each
caller wraps this in its own ``try/except``. (Historically ``handle_rag_search``
forwarded ``filters=``/``agent_id=``, which ``search()`` rejects, so every MCP
``rag_search`` call errored; fixed in hcx-ctx-06 by mapping ``source_type`` onto
``source_types`` and dropping the unsupported ``child_id``.) Only ``search_rag``
consumes ``clamp_unit`` — the MCP handler normalizes via
``SearchResult.to_dict()`` rounding instead.
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
    ``try/except`` and degrades to an empty/​error result. One of them is
    meaningful — ``retriever.EmbeddingUnavailableError`` says the query was
    never embedded, so an empty list here means "no match" and that exception
    means "retrieval did not run". Do not collapse the two.
    """
    retriever = retriever_cls(tenant_id=tenant_id)
    return retriever.search(query, **search_kwargs) or []
