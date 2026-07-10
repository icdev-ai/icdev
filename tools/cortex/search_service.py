# CUI // SP-CTI
"""Cortex search backend adapters — the retrieval layer of unified Cortex Search.

The four existing search backends return four incompatible native shapes:

- ``rag``   — ``tools/rag/retriever.py`` ``RAGRetriever.search()`` returns
  ``SearchResult`` dataclasses (vector/bm25/rerank/final scores).
- ``graph`` — ``tools/knowledge_graph/graph_rag.py`` module-level
  ``retrieve()`` returns a dict with scored node dicts. The module is
  sqlite3-backed; it is treated as a read-only backend isolated behind its
  adapter (PG-primary migration is out of scope here).
- ``dic``   — ``tools/document_intelligence/search_engine.py``
  ``DICSearchEngine.search()`` returns ``DICSearchResult`` with a mandatory
  citation pack and clearance-aware filtering.
- ``kb``    — the ``search_knowledge`` keyword KB
  (``tools/mcp/knowledge_server.py``) returns pattern dicts.

Each adapter takes ``(query, top_k, ctx)`` and normalizes its backend's
native hits into ``CortexSearchResult`` (score clamped to [0, 1], native
scores preserved verbatim in ``raw_scores``). Adapters are
exception-isolated: a failing backend logs a warning and returns ``[]``,
never breaking the other backends.

Backends are imported lazily from the same namespace root this module was
loaded from (``tools.*`` shim or canonical ``icdev.tools.*``) so both
namespaces — and monkeypatched tests — resolve consistently.
"""
from __future__ import annotations

import importlib
import logging
from typing import Optional

from .schemas import CORTEX_BACKENDS, Citation, CortexContext, CortexSearchResult

logger = logging.getLogger(__name__)

# "tools" when loaded via the shim namespace, "icdev.tools" when canonical.
_NS = __name__.rsplit(".cortex.", 1)[0]

_SNIPPET_CHARS = 200


def _backend(module: str):
    """Import a backend module from this module's own namespace root."""
    return importlib.import_module(f"{_NS}.{module}")


def _clamp(value) -> float:
    """Coerce to float and clamp to [0, 1]; unparseable values become 0.0."""
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _peak_norm(raw_scores: list) -> float:
    """Divisor that maps a raw score set into [0, 1] preserving order.

    Returns the peak score when it exceeds 1.0 (additive scoring formulas
    like the graph profile weights can sum past 1.0), else 1.0 so already
    normalized backends pass through unchanged.
    """
    peak = 0.0
    for s in raw_scores:
        try:
            peak = max(peak, float(s))
        except (TypeError, ValueError):
            continue
    return peak if peak > 1.0 else 1.0


# ---------------------------------------------------------------------------
# Backend adapters
# ---------------------------------------------------------------------------


def search_rag(
    query: str,
    top_k: int = 5,
    ctx: Optional[CortexContext] = None,
) -> list[CortexSearchResult]:
    """RAG two-stage retriever -> CortexSearchResult (backend='rag').

    Passes ``ctx.tenant_id`` into ``RAGRetriever`` so vector-store filters
    stay tenant-scoped. Vector/bm25/time-decay/rerank/final native scores are
    preserved in ``raw_scores``; ``final_score`` (already in [0, 1] for both
    RRF and weighted-sum fusion) becomes the normalized score.
    """
    ctx = ctx or CortexContext()
    try:
        retriever_mod = _backend("rag.retriever")
        retriever = retriever_mod.RAGRetriever(tenant_id=ctx.tenant_id)
        native = retriever.search(query, top_k=top_k) or []
        out = []
        for r in native:
            content = r.content or ""
            out.append(
                CortexSearchResult(
                    content=content,
                    score=_clamp(r.final_score),
                    backend="rag",
                    strategy="hybrid",
                    citation=Citation(
                        source_id=r.source_id or r.chunk_id,
                        source_type="rag_chunk",
                        source_table=r.source_table,
                        title=r.source_type,
                        snippet=content[:_SNIPPET_CHARS],
                        classification=r.classification or ctx.classification,
                    ),
                    raw_scores={
                        "vector": r.score,
                        "bm25": r.bm25_score,
                        "time_decay": r.time_decay_score,
                        "rerank": r.rerank_score,
                        "final": r.final_score,
                    },
                    metadata={
                        "chunk_id": r.chunk_id,
                        "chunk_index": r.chunk_index,
                        "source_type": r.source_type,
                        "tier": r.tier,
                        "tenant_id": ctx.tenant_id,
                    },
                )
            )
        return out
    except Exception as exc:
        logger.warning("Cortex rag backend failed: %s", exc)
        return []


def search_graph(
    query: str,
    top_k: int = 10,
    ctx: Optional[CortexContext] = None,
) -> list[CortexSearchResult]:
    """Knowledge-graph GraphRAG -> CortexSearchResult (backend='graph').

    graph_rag is sqlite3-backed and has no tenant/classification filtering —
    it is wrapped read-only behind this adapter. Node scores are additive
    (edge + centrality + recency + bonuses) and can exceed 1.0, so the set is
    peak-normalized before clamping; raw values stay in ``raw_scores``.
    """
    try:
        graph_rag = _backend("knowledge_graph.graph_rag")
        result = graph_rag.retrieve(query, top_k=top_k, compress=False) or {}
        if result.get("status") != "ok":
            logger.warning(
                "Cortex graph backend error: %s", result.get("context", "")
            )
            return []
        nodes = result.get("nodes") or []
        norm = _peak_norm([n.get("score") for n in nodes])
        profile = str(result.get("profile") or "")
        out = []
        for n in nodes:
            label = str(n.get("label") or "")
            entity_type = str(n.get("entity_type") or "")
            props = n.get("properties") or ""
            content = f"{label} ({entity_type})" if entity_type else label
            if props:
                content = f"{content}: {props}"
            raw_scores = {
                k: n[k]
                for k in ("score", "centrality", "hybrid_rank")
                if n.get(k) is not None
            }
            out.append(
                CortexSearchResult(
                    content=content,
                    score=_clamp((n.get("score") or 0.0) / norm),
                    backend="graph",
                    strategy=profile,
                    citation=Citation(
                        source_id=str(n.get("id") or ""),
                        source_type="kg_node",
                        source_table="kg_nodes",
                        title=label,
                        snippet=content[:_SNIPPET_CHARS],
                    ),
                    raw_scores=raw_scores,
                    metadata={
                        "graph_id": n.get("graph_id", ""),
                        "entity_type": entity_type,
                        "is_neighbor": bool(n.get("is_neighbor")),
                        "profile": profile,
                    },
                )
            )
        return out
    except Exception as exc:
        logger.warning("Cortex graph backend failed: %s", exc)
        return []


def search_dic(
    query: str,
    top_k: int = 10,
    ctx: Optional[CortexContext] = None,
) -> list[CortexSearchResult]:
    """DIC grounded search -> CortexSearchResult (backend='dic').

    Passes ``ctx.tenant_id`` into ``DICSearchEngine`` and ``ctx.classification``
    as the caller clearance so DIC's clearance-aware ranking drops
    above-clearance documents before the cap. The document classification is
    preserved as ``Citation.clearance_required``.
    """
    ctx = ctx or CortexContext()
    try:
        se = _backend("document_intelligence.search_engine")
        engine = se.DICSearchEngine(tenant_id=ctx.tenant_id or "default")
        native = engine.search(
            query, top_k=top_k, clearance=ctx.classification or None
        ) or []
        norm = _peak_norm([r.score for r in native])
        out = []
        for r in native:
            content = r.content or ""
            classification = r.citation.classification if r.citation else "CUI"
            out.append(
                CortexSearchResult(
                    content=content,
                    score=_clamp(r.score / norm),
                    backend="dic",
                    strategy="grounded",
                    citation=Citation(
                        source_id=r.doc_id,
                        source_type="dic_document",
                        source_table="dic_documents",
                        title=r.doc_title,
                        snippet=content[:_SNIPPET_CHARS],
                        url=f"/document-intelligence/doc/{r.doc_id}" if r.doc_id else "",
                        classification=classification,
                        clearance_required=classification,
                    ),
                    raw_scores={
                        "score": r.score,
                        "attribution_score": r.attribution_score,
                        "attribution_pct": r.attribution_pct,
                    },
                    metadata={
                        "chunk_id": r.chunk_id,
                        "collection_id": r.collection_id,
                        "page": r.page,
                        "section": r.section,
                        "matched_terms": list(r.matched_terms or []),
                        "kg_path": list(r.kg_path or []),
                        "sha256": r.sha256,
                        "tenant_id": ctx.tenant_id,
                    },
                )
            )
        return out
    except Exception as exc:
        logger.warning("Cortex dic backend failed: %s", exc)
        return []


def search_kb(
    query: str,
    top_k: int = 10,
    ctx: Optional[CortexContext] = None,
) -> list[CortexSearchResult]:
    """Keyword knowledge base (search_knowledge) -> CortexSearchResult (backend='kb').

    Pattern ``confidence`` (already 0-1) becomes the normalized score;
    ``use_count`` is preserved in ``raw_scores``.
    """
    try:
        ks = _backend("mcp.knowledge_server")
        resp = ks.handle_search_knowledge({"query": query, "limit": top_k}) or {}
        out = []
        for p in resp.get("results") or []:
            description = str(p.get("description") or "")
            solution = str(p.get("solution") or "")
            if description and solution:
                content = f"{description}\nSolution: {solution}"
            else:
                content = description or solution
            out.append(
                CortexSearchResult(
                    content=content,
                    score=_clamp(p.get("confidence")),
                    backend="kb",
                    strategy="keyword",
                    citation=Citation(
                        source_id=str(p.get("id") or ""),
                        source_type="kb_entry",
                        source_table="knowledge_patterns",
                        title=str(p.get("name") or ""),
                        snippet=content[:_SNIPPET_CHARS],
                    ),
                    raw_scores={
                        "confidence": p.get("confidence") or 0.0,
                        "use_count": p.get("use_count") or 0,
                    },
                    metadata={
                        "pattern_type": p.get("pattern_type", ""),
                        "detection_rule": p.get("detection_rule", ""),
                    },
                )
            )
        return out
    except Exception as exc:
        logger.warning("Cortex kb backend failed: %s", exc)
        return []


# Dispatch table for the Cortex facade — keys match CORTEX_BACKENDS.
BACKEND_ADAPTERS = {
    "rag": search_rag,
    "graph": search_graph,
    "dic": search_dic,
    "kb": search_kb,
}


def search_all(
    query: str,
    top_k: int = 5,
    ctx: Optional[CortexContext] = None,
    backends: Optional[list] = None,
) -> list[CortexSearchResult]:
    """Run the requested backends (default: all four) and merge results.

    Returns the combined list sorted by normalized score descending. Each
    adapter is already exception-isolated, so one failing backend degrades
    to zero results without affecting the others.
    """
    merged: list[CortexSearchResult] = []
    for name in backends or CORTEX_BACKENDS:
        adapter = BACKEND_ADAPTERS.get(name)
        if adapter is None:
            logger.warning("Cortex search: unknown backend %r skipped", name)
            continue
        merged.extend(adapter(query, top_k=top_k, ctx=ctx))
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged
