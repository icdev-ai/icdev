# CUI // SP-CTI
"""Cortex search backend adapters — the retrieval layer of unified Cortex Search.

The four RETRIEVAL backends return four incompatible native shapes:

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

A fifth backend, ``sme``, does not retrieve at all: it asks an ACE
domain-expert persona (``tools/ace/sme_registry.py`` +
``tools/ace/persona_query.py``) for an OPINION, authored by a model at query
time. It normalizes into the same dataclass, so the split that keeps an opinion
out of a verdict is carried by ADVISORY_BACKENDS / EVIDENTIARY_BACKENDS in
``tools/cortex/schemas.py`` and by ``is_advisory()`` below — never by the shape
of the result.

Each adapter takes ``(query, top_k, ctx)`` and normalizes its backend's
native hits into ``CortexSearchResult`` (score clamped to [0, 1], native
scores preserved verbatim in ``raw_scores``). Adapters are
exception-isolated: a failing backend logs and returns an EMPTY
``BackendResults`` whose ``.errors`` says what went wrong, never breaking the
other backends. That annotation is what lets a caller tell "the backend died"
from "the corpus matched nothing" — the two used to be the same empty list.

Backends are imported lazily from the same namespace root this module was
loaded from (``tools.*`` shim or canonical ``icdev.tools.*``) so both
namespaces — and monkeypatched tests — resolve consistently.

On top of the adapters sits the strategy router (``search()``): queries are
classified (pattern rules + the RAG taxonomy classifier) and routed to the
right backend(s), with ambiguous queries fanning out in parallel under
per-backend timeouts from ``args/cortex_config.yaml``.
"""
from __future__ import annotations

import concurrent.futures
import importlib
import os
import re
import threading
import time
from typing import Optional

from tools.logging.icdev_logger import get_logger
from tools.rag.retriever_common import clamp_unit, run_rag_search

from .config import load_cortex_config, resolve_strategy_weights
from .schemas import (
    ADVISORY_BACKENDS,
    CORTEX_BACKENDS,
    EVIDENTIARY_BACKENDS,
    Citation,
    CortexContext,
    CortexSearchResult,
)

logger = get_logger(__name__)

# "tools" when loaded via the shim namespace, "icdev.tools" when canonical.
_NS = __name__.rsplit(".cortex.", 1)[0]

_SNIPPET_CHARS = 200

# ---------------------------------------------------------------------------
# Shared search fan-out thread pool.
#
# _run_backends used to create a fresh ThreadPoolExecutor per call and shut it
# down with wait=False. A backend that exceeded its timeout left its worker
# thread running (Python cannot force-kill a thread), so under sustained
# timeouts every new call spawned MORE threads that never went away — an
# unbounded leak. A single process-wide, bounded pool fixes this: threads are
# reused across calls and the total is capped. A timed-out task's thread simply
# returns to the pool once the (already time-limited) adapter finally completes,
# instead of accumulating. Size via CORTEX_SEARCH_MAX_WORKERS or the first
# call's fan_out.max_workers; default 16.
_SEARCH_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_SEARCH_EXECUTOR_LOCK = threading.Lock()
_DEFAULT_SEARCH_MAX_WORKERS = 16


def _get_search_executor(search_cfg: Optional[dict] = None) -> concurrent.futures.ThreadPoolExecutor:
    """Return the lazily-initialized, process-wide search fan-out pool.

    Thread-safe (double-checked lock). The pool is sized once, on first use,
    from ``fan_out.max_workers`` (if a config is supplied) else the
    ``CORTEX_SEARCH_MAX_WORKERS`` env var else the default. It is intentionally
    never shut down per call — it is shared across every search.
    """
    global _SEARCH_EXECUTOR
    if _SEARCH_EXECUTOR is None:
        with _SEARCH_EXECUTOR_LOCK:
            if _SEARCH_EXECUTOR is None:
                cfg_workers = None
                if search_cfg:
                    cfg_workers = (search_cfg.get("fan_out") or {}).get("max_workers")
                try:
                    max_workers = int(
                        cfg_workers
                        or os.environ.get("CORTEX_SEARCH_MAX_WORKERS")
                        or _DEFAULT_SEARCH_MAX_WORKERS
                    )
                except (ValueError, TypeError):
                    max_workers = _DEFAULT_SEARCH_MAX_WORKERS
                _SEARCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, max_workers),
                    thread_name_prefix="cortex-search",
                )
    return _SEARCH_EXECUTOR


def _backend(module: str):
    """Import a backend module from this module's own namespace root."""
    return importlib.import_module(f"{_NS}.{module}")


class BackendResults(list):
    """A result list that also carries the backends that FAILED (ctx-perf-04).

    A backend that died returns zero hits — byte-identical to a backend that
    legitimately matched nothing. That is how a dead embedding provider reached
    the chat user as "No matching results were found across the Cortex
    backends": an infrastructure failure rendered as an answer about the corpus.

    This is a plain ``list`` subclass, so every consumer that treats the value
    as a list (indexing, ``len``, iteration, ``extend``) is unaffected and any
    code that rebuilds the list simply drops the annotation. A consumer that
    wants to tell "failed" from "empty" reads ``getattr(results, "errors", ())``.
    """

    def __init__(self, items=(), errors=None):
        super().__init__(items)
        self.errors: list[dict] = list(errors or [])


def _embedding_error_cls():
    """The retriever's EmbeddingUnavailableError, resolved in this namespace.

    Returns the empty tuple when the retriever module itself cannot be imported
    — ``isinstance(exc, ())`` is False, so the caller falls through to the
    generic backend-failure path.
    """
    try:
        return _backend("rag.retriever").EmbeddingUnavailableError
    except Exception:  # noqa: BLE001 — retriever unimportable; generic path is right
        return ()


def _clamp(value) -> float:
    """Coerce to float and clamp to [0, 1]; unparseable values become 0.0.

    Thin delegator to the shared ``clamp_unit`` (tools/rag/retriever_common.py)
    so the unit-interval normalization has a single home; kept as ``_clamp`` so
    the graph/dic/kb adapters' call sites stay unchanged.
    """
    return clamp_unit(value)


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


_DEFAULT_RRF_K = 60


def _fusion_ident(r) -> str:
    """Backend-agnostic identity so the SAME source retrieved by two backends
    fuses into one entry. Prefer the citation source_id; fall back to content."""
    src = getattr(getattr(r, "citation", None), "source_id", "") or ""
    return str(src) if src else (getattr(r, "content", "") or "")


def _rrf_fuse(results: list, k: int = _DEFAULT_RRF_K, weights: Optional[dict] = None) -> list:
    """Fuse multi-backend results by weighted Reciprocal Rank Fusion.

    Per-backend raw scores are not comparable across backends (each backend
    peak-normalizes its own set), so a plain score-sort over the concatenated
    list is biased toward whichever backend emits larger numbers. RRF instead
    ranks each backend's list independently and scores each item by
    ``sum(weight / (k + rank))`` over the backends that returned it — rank-based,
    so scale-free, and it rewards items surfaced by multiple backends.

    ``weights`` is ``search.strategy_weights`` from args/cortex_config.yaml
    (normalized by ``config.resolve_strategy_weights``); a backend with no entry
    weighs 1.0, which makes an absent/empty mapping identical to unweighted RRF.
    This is the formula the YAML has always documented — until ctx-enf-03 the
    code dropped the weight term, so tuning ``rag: 1.0`` against ``kb: 0.6``
    changed nothing.

    The item's raw ``.score`` is left untouched (CRAG + callers still read it);
    the fused score is recorded in ``raw_scores['rrf']`` and the list is ordered
    by it. Deterministic: ties break by raw score then identity.
    """
    if not results:
        return []
    weights = weights or {}
    by_backend: dict = {}
    for r in results:
        by_backend.setdefault(getattr(r, "backend", "") or "", []).append(r)

    fused: dict = {}  # ident -> {"result": best_repr, "rrf": float}
    # NB: named `backend_name`, not `_backend` — the module-level `_backend()`
    # importer would be shadowed, and this loop now READS the value.
    for backend_name, items in by_backend.items():
        weight = weights.get(backend_name, 1.0)
        ranked = sorted(items, key=lambda r: getattr(r, "score", 0.0) or 0.0, reverse=True)
        for rank, r in enumerate(ranked, start=1):
            ident = _fusion_ident(r)
            contrib = weight / (k + rank)
            cur = fused.get(ident)
            if cur is None:
                fused[ident] = {"result": r, "rrf": contrib}
            else:
                cur["rrf"] += contrib
                # Keep the higher raw-score representative for display.
                if (getattr(r, "score", 0.0) or 0.0) > (getattr(cur["result"], "score", 0.0) or 0.0):
                    cur["result"] = r

    ordered = []
    for entry in fused.values():
        r = entry["result"]
        rrf = round(entry["rrf"], 8)
        try:
            r.raw_scores = dict(getattr(r, "raw_scores", None) or {})
            r.raw_scores["rrf"] = rrf
        except Exception:  # noqa: BLE001 — immutable/edge result; ordering still works
            pass
        ordered.append((rrf, getattr(r, "score", 0.0) or 0.0, _fusion_ident(r), r))
    ordered.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [t[3] for t in ordered]


# ---------------------------------------------------------------------------
# Adaptive complexity pre-routing (agx-rag-01) — adopted here by trust-self-03
# ---------------------------------------------------------------------------
#
# ``AdaptiveRetriever`` WRAPS ``RAGRetriever``, so it can never be reached from
# inside ``retriever.search()`` without a cycle. ``tools/rag/toggle_harness.py``
# therefore reported ``adaptive_routing`` as WRAPPER-UNADOPTED: not dead code, but
# a behaviour with no caller — "a product decision about which surface gets it".
# This is that caller. ``search_rag`` is the exact site that constructs a
# tenant-scoped ``RAGRetriever`` and runs its two-stage search, which is what the
# wrapper wraps.
#
# CITATION SAFETY. ``requires_citations`` is True here and is deliberately NOT
# caller-configurable: every ``CortexSearchResult`` carries a ``Citation`` and
# the facade suppresses uncited content (tools/cortex/schemas.py), so this IS a
# citation surface. The wrapper's ``none``/skip route — which answers with no
# retrieved evidence at all — is consequently unavailable at this seam, enforced
# in the wrapper rather than documented as a caveat.
#
# The consequence is worth stating plainly rather than discovering later: on this
# surface adaptive routing buys a WIDER candidate set for compositional queries,
# never a skipped retrieval call. ``measure_savings()`` run with the Cortex
# posture reports ``retrieval_calls_saved: 0`` by construction — that is the
# correct number for a citation surface, not a null result.
_CORTEX_REQUIRES_CITATIONS = True


def _rag_retrieve(retriever_mod, query: str, top_k: int, ctx: CortexContext) -> tuple:
    """Retrieve for the ``rag`` backend, through AdaptiveRetriever when enabled.

    Returns ``(native_results, routing)``; ``routing`` is the wrapper's decision
    record, or None when ``rag.adaptive_routing.enabled`` is false — in which
    case this is byte-for-byte the pre-existing ``run_rag_search()`` call.
    """
    # Imported statically rather than through _backend() so the adoption is
    # visible to the AST scan in toggle_harness._repo_importers: a dynamic
    # importlib call would leave the toggle reading WRAPPER-UNADOPTED while
    # actually being consumed, which is the exact failure that harness exists to
    # catch. The retriever INSTANCE is still resolved via _backend(), so tenant
    # scoping and namespace/monkeypatch consistency are unaffected.
    from tools.rag.adaptive_router import AdaptiveRetriever

    # Constructed twice on the enabled path, and that is intentional. The first
    # reads only the (memoized) rag config to answer `enabled`; the second
    # injects a TENANT-SCOPED retriever, because the wrapper's own lazy
    # _get_retriever() builds RAGRetriever() with no tenant_id and would drop
    # the vector-store tenant filter.
    adaptive = AdaptiveRetriever()
    if not adaptive.enabled:
        return run_rag_search(
            retriever_mod.RAGRetriever, query, tenant_id=ctx.tenant_id, top_k=top_k,
            surface="chat_rag",
        ), None

    adaptive = AdaptiveRetriever(
        retriever=retriever_mod.RAGRetriever(tenant_id=ctx.tenant_id)
    )
    outcome = adaptive.retrieve(
        query, requires_citations=_CORTEX_REQUIRES_CITATIONS, top_k=top_k,
        # Forwarded verbatim to RAGRetriever.search() through the wrapper's
        # **kwargs, so the surface scoping survives the adaptive path too.
        surface="chat_rag",
    )
    routing = {
        "route": outcome.get("route", ""),
        "complexity": outcome.get("complexity", ""),
        "source": outcome.get("source", ""),
        "retrieved": bool(outcome.get("retrieved", True)),
    }
    return outcome.get("results") or [], routing


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

    Still exception-isolated — a failure never breaks the other backends — but
    the failure is now RECORDED on the returned ``BackendResults`` instead of
    vanishing into an empty list, so the caller can distinguish a dead
    embedding provider from a corpus that matched nothing (ctx-perf-04).

    Retrieval runs through ``AdaptiveRetriever`` when ``rag.adaptive_routing``
    is enabled — see :func:`_rag_retrieve` for the citation-safety posture and
    why the skip route cannot fire on this surface. Default off, in which case
    this is the unchanged single-pass call.
    """
    ctx = ctx or CortexContext()
    try:
        retriever_mod = _backend("rag.retriever")
        native, routing = _rag_retrieve(retriever_mod, query, top_k, ctx)
        out = []
        for r in native:
            content = r.content or ""
            metadata = {
                "chunk_id": r.chunk_id,
                "chunk_index": r.chunk_index,
                "source_type": r.source_type,
                "tier": r.tier,
                "tenant_id": ctx.tenant_id,
            }
            # Only present when the toggle is on, so an enabled deployment's
            # routing decision is auditable instead of invisible.
            if routing:
                metadata["adaptive_routing"] = dict(routing)
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
                    metadata=metadata,
                )
            )
        return out
    except Exception as exc:
        if isinstance(exc, _embedding_error_cls()):
            logger.error(
                "Cortex rag backend could not embed the query: %s — reporting a "
                "retrieval FAILURE, not zero results",
                exc,
            )
            stage = "embedding"
        else:
            logger.warning("Cortex rag backend failed: %s", exc)
            stage = "error"
        return BackendResults(
            [], errors=[{"backend": "rag", "stage": stage, "message": str(exc)}]
        )


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
        return BackendResults(
            [],
            errors=[{"backend": "graph", "stage": "error", "message": str(exc)}],
        )


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
        return BackendResults(
            [],
            errors=[{"backend": "dic", "stage": "error", "message": str(exc)}],
        )


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
        return BackendResults(
            [],
            errors=[{"backend": "kb", "stage": "error", "message": str(exc)}],
        )


def _currency_store():
    """The entity-currency store, resolved from whichever namespace holds it.

    ``tools/currency/`` has no ``icdev/tools/`` mirror, so ``_backend`` cannot
    find it when this module was loaded canonically. Falling back to the shim
    namespace keeps the backend ALIVE in both trees rather than reporting a
    dead store for one that is present; a genuinely missing module still raises
    and is reported as a failure.
    """
    try:
        return _backend("currency.entity_currency")
    except ModuleNotFoundError:
        return importlib.import_module("tools.currency.entity_currency")


def _defacto_learner():
    """The de-facto standards learner, same two-namespace resolution."""
    try:
        return _backend("doc_modernization.defacto_learner")
    except ModuleNotFoundError:
        return importlib.import_module("tools.doc_modernization.defacto_learner")


# Score bands for the currency backend, HIGH to LOW. The authority order is
# structural rather than emergent: a curated catalog assertion cannot score
# below an EOL feed's however confident the feed is, and no learner row can
# reach either — which is the same rule args/entity_currency.yaml states for
# read-time resolution ("a tie-break that a bumped prior can overturn is not
# authority") applied to the ranking a caller actually reads.
_CURRENCY_BANDS = {
    "curated": (0.75, 1.00),   # an authoritative source (the curated catalog)
    "feed": (0.45, 0.75),      # an external EOL feed
    "learner": (0.10, 0.45),   # de-facto / learned corroboration
}


def _band_score(band: str, quality: float) -> float:
    """Place a [0,1] quality inside its band, so bands never overlap."""
    low, high = _CURRENCY_BANDS.get(band, (0.0, 1.0))
    return low + (high - low) * _clamp(quality)


def _currency_content(view: dict) -> str:
    """One sentence a human (or an LLM) can read the verdict off."""
    label = str(view.get("entity_label") or view.get("entity_key") or "")
    parts = [f"{label} ({view.get('entity_type')})"]
    version = str(view.get("entity_version") or "")
    if version:
        parts.append(f"version {version}")
    text = " ".join(parts)
    text += (
        f" — {view.get('verdict')} per {view.get('source')}"
        f" (as of {str(view.get('as_of') or '')[:10]})."
    )
    for field_name, prefix in (("eol_date", "End of life"), ("eos_date", "End of support")):
        if view.get(field_name):
            text += f" {prefix}: {view[field_name]}."
    if view.get("superseded_by"):
        text += f" Superseded by: {view['superseded_by']}."
    if view.get("conflict"):
        disagree = ", ".join(
            f"{o.get('source')}={o.get('verdict')}" for o in (view.get("others") or [])
        )
        text += f" Sources disagree — also reported: {disagree}."
    return text


def _currency_assertion_result(view: dict, ctx: CortexContext) -> CortexSearchResult:
    """One resolved entity-currency assertion -> CortexSearchResult."""
    authoritative = bool(view.get("authoritative"))
    band = "curated" if authoritative else "feed"
    match = float(view.get("match") or 0.0)
    confidence = _clamp(view.get("confidence"))
    content = _currency_content(view)
    provenance = view.get("provenance") or {}
    return CortexSearchResult(
        content=content,
        # The declared prior is HALF the quality, never the whole of it: it is a
        # constant per source, so on its own it would rank every row from one
        # source identically regardless of what the caller asked about.
        score=_band_score(band, 0.5 * match + 0.5 * confidence),
        backend="currency",
        strategy="assertion",
        citation=Citation(
            source_id=str(provenance.get("record_id") or ""),
            source_type="currency_assertion",
            # The row the verdict actually came from, not the store that
            # aggregates it — a citation that names the aggregator sends a
            # reader to a copy rather than to the evidence.
            source_table=str(provenance.get("table") or "entity_currency"),
            title=str(view.get("entity_label") or view.get("entity_key") or ""),
            snippet=content[:_SNIPPET_CHARS],
            classification=str(view.get("classification") or ctx.classification or "CUI"),
        ),
        raw_scores={
            "confidence": view.get("confidence"),
            "match": match,
            "band": band,
        },
        metadata={
            "lane": "assertion",
            "store_table": "entity_currency",
            "provenance_id": provenance.get("id"),
            "entity_type": view.get("entity_type"),
            "entity_key": view.get("entity_key"),
            "namespace": view.get("namespace"),
            "entity_version": view.get("entity_version"),
            "verdict": view.get("verdict"),
            "superseded_by": view.get("superseded_by"),
            "eol_date": view.get("eol_date"),
            "eos_date": view.get("eos_date"),
            "source": view.get("source"),
            "authoritative": authoritative,
            "as_of": view.get("as_of"),
            # Disagreement travels with the answer (see entity_currency.resolve).
            "conflict": bool(view.get("conflict")),
            "sources_consulted": list(view.get("sources_consulted") or []),
            "others": [
                {
                    "source": o.get("source"),
                    "verdict": o.get("verdict"),
                    "confidence": o.get("confidence"),
                    "as_of": o.get("as_of"),
                }
                for o in (view.get("others") or [])
            ],
            "scan_truncated": bool(view.get("scan_truncated")),
            "tenant_id": ctx.tenant_id,
        },
    )


def _currency_learner_result(row: dict, ctx: CortexContext) -> CortexSearchResult:
    """One learned de-facto standard -> CortexSearchResult (corroboration)."""
    share = float(row.get("share_pct") or 0.0)
    match = float(row.get("match") or 0.0)
    vendor = str(row.get("vendor") or "").strip()
    product = str(row.get("product") or "").strip()
    label = f"{vendor} {product}".strip()
    content = (
        f"{label} is the de-facto {row.get('category')} in "
        f"{row.get('domain')}: {share:.1f}% of the {row.get('source_feed')} feed "
        f"({row.get('deploy_count')} occurrences, evidence: "
        f"{row.get('evidence_kind') or 'unlabelled'}). Corroboration only — the "
        "curated catalog remains authoritative."
    )
    return CortexSearchResult(
        content=content,
        score=_band_score("learner", 0.5 * match + 0.5 * min(1.0, share / 100.0)),
        backend="currency",
        strategy="defacto",
        citation=Citation(
            source_id=str(row.get("id") or ""),
            source_type="defacto_standard",
            source_table="docmod_defacto_standards",
            title=label,
            snippet=content[:_SNIPPET_CHARS],
            classification=str(row.get("classification") or ctx.classification or "CUI"),
        ),
        raw_scores={
            "share_pct": row.get("share_pct"),
            "weighted_score": row.get("weighted_score"),
            "deploy_count": row.get("deploy_count"),
            "match": match,
            "band": "learner",
        },
        metadata={
            "lane": "learner",
            "domain": row.get("domain"),
            "category": row.get("category"),
            "vendor": vendor,
            "product": product,
            "entity_version": row.get("version"),
            # WHICH feed and WHAT CLASS of evidence — never merged, because a
            # modelled design and an observed estate are different claims.
            "source_feed": row.get("source_feed"),
            "evidence_kind": row.get("evidence_kind"),
            "precedence": row.get("precedence"),
            "computed_at": row.get("computed_at"),
            "tenant_id": ctx.tenant_id,
        },
    )


def search_currency(
    query: str,
    top_k: int = 5,
    ctx: Optional[CortexContext] = None,
) -> list[CortexSearchResult]:
    """Entity currency -> CortexSearchResult (backend='currency') (cef-bck-01).

    Answers "is this entity still current?" over TWO lanes, kept apart on
    purpose:

    * ASSERTION — ``tools/currency/entity_currency.py``, the domain-agnostic
      store that already carries the curated catalog (authoritative), the
      endoflife.date feed and the hardware EOL feed, and resolves them under
      the authority policy declared in args/entity_currency.yaml.
    * LEARNER — ``docmod_defacto_standards``, what the inventory feeds learned
      is actually fielded. Corroboration and tie-breaker, never authority.

    Ranking is BANDED (see ``_CURRENCY_BANDS``) so the authority order holds
    structurally: curated above feed above learner, whatever the numbers inside
    a band do.

    Each lane is isolated separately: a dead learner table still returns the
    store's hits, with the failure recorded on ``BackendResults.errors``. An
    empty result with EMPTY errors means the corpus genuinely matched nothing —
    that distinction is the point of the annotation and this adapter never
    blurs it.
    """
    ctx = ctx or CortexContext()
    results: list[CortexSearchResult] = []
    errors: list[dict] = []

    try:
        views = _currency_store().search(query, limit=top_k) or []
        results.extend(_currency_assertion_result(v, ctx) for v in views)
    except Exception as exc:  # noqa: BLE001 — never raise; report instead
        logger.warning("Cortex currency backend (assertion lane) failed: %s", exc)
        errors.append({"backend": "currency", "stage": "store", "message": str(exc)})

    try:
        rows = _defacto_learner().search(query, limit=top_k) or []
        results.extend(_currency_learner_result(r, ctx) for r in rows)
    except Exception as exc:  # noqa: BLE001 — never raise; report instead
        logger.warning("Cortex currency backend (learner lane) failed: %s", exc)
        errors.append(
            {"backend": "currency", "stage": "corroboration", "message": str(exc)}
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return BackendResults(results[:max(1, int(top_k or 1))], errors=errors)
# ---------------------------------------------------------------------------
# Advisory backend (cef-bck-03) — an OPINION, never a verdict
# ---------------------------------------------------------------------------

#: The only bundle this backend will ever ask ``ensure_sme`` for. ``advisory``
#: ships ``folder_access: []`` and ``icdev_tools: []`` in
#: args/ace/sme_capability_bundles.yaml, so a persona minted on this path cannot
#: write or execute anything. Hard-coded rather than caller-configurable: a
#: search backend is not a place to hand out agency, and "which bundle" is a
#: human decision (see the promotion note in that YAML).
SME_CAPABILITY_BUNDLE = "advisory"

#: An opinion carries no retrieval confidence, so ``score`` is 0.0 — and that
#: 0.0 must not be read as "measured a terrible match". Three things keep it from
#: being read that way, none of them a tooltip: ``metadata["advisory"]`` says the
#: result is an opinion, ``raw_scores["scored"] = False`` says no score was ever
#: computed, and ``search.strategy_weights.sme = 0.0`` demotes it in RRF anyway.
#: ``_corrective_pass`` also excludes advisory results from the CRAG trigger —
#: an opinion is not evidence that retrieval succeeded OR that it failed.
_SME_SCORE = 0.0


def is_advisory(result) -> bool:
    """True when *result* is an OPINION rather than retrieved evidence.

    The one predicate every consumer that forms a VERDICT must call.

    Two independent signals, either of which is sufficient: the
    ``metadata["advisory"]`` flag the adapter stamps, and membership of
    ``result.backend`` in ADVISORY_BACKENDS. Metadata is a plain dict that any
    consumer may rebuild or drop (``_routed_pass`` already rewrites keys in it);
    ``backend`` is what the result IS. Requiring both would let a dropped flag
    promote an opinion into a verdict, so this is deliberately an ``or``.
    """
    if (getattr(result, "backend", "") or "") in ADVISORY_BACKENDS:
        return True
    return bool((getattr(result, "metadata", None) or {}).get("advisory"))


def search_sme(
    query: str,
    top_k: int = 10,
    ctx: Optional[CortexContext] = None,
) -> list[CortexSearchResult]:
    """ACE domain-expert opinion -> CortexSearchResult (backend='sme').

    Two ACE calls, in order:

    1. ``sme_registry.ensure_sme`` resolves the domain to a role — reusing one
       of the ~90 catalog roles when it covers the domain, and minting a new
       ``advisory`` persona only when none does.
    2. ``persona_query.query_persona`` asks THAT role the question, in one
       synchronous LLM call framed by its SOUL.md identity.

    ``top_k`` is accepted for adapter-signature parity and deliberately unused:
    one expert gives one opinion, so this backend returns at most one result.
    Asking for ten would mean ten LLM calls to manufacture a majority, which is
    the opposite of what an advisory rung is for.

    WHAT THIS IS NOT. The returned text is an opinion an LLM authored at query
    time, not a row that existed before the query. It is marked
    ``metadata["advisory"] = True`` and must never become a deterministic
    verdict — see the ADVISORY_BACKENDS note in tools/cortex/schemas.py. The
    persona is only ever *read* here (identity preamble + one completion); this
    backend never launches a team, so a reused catalog role's tool permissions
    are never exercised.

    Degrades, never fabricates: if the domain cannot be resolved or no provider
    can serve the completion, the return is a :class:`BackendResults` with a
    populated ``.errors`` and NO result. An empty opinion is not a neutral
    opinion.
    """
    ctx = ctx or CortexContext()
    q = (query or "").strip()
    if not q:
        return BackendResults(
            [],
            errors=[{
                "backend": "sme",
                "stage": "input",
                "message": "empty query — no question to put to an expert",
            }],
        )

    # Stage 1 — resolve/mint the persona. ctx.domain (a configured domain lens)
    # is the better SME domain when present: it is already the canonical label
    # for this call, so two differently-worded questions in one lens converge on
    # one expert instead of minting a near-duplicate per phrasing.
    domain_description = (ctx.domain or "").strip() or q
    try:
        registry = _backend("ace.sme_registry")
        sme = registry.ensure_sme(
            domain_description, capability_bundle=SME_CAPABILITY_BUNDLE
        )
    except Exception as exc:
        logger.warning("Cortex sme backend failed to resolve a persona: %s", exc)
        return BackendResults(
            [],
            errors=[{"backend": "sme", "stage": "ensure_sme", "message": str(exc)}],
        )

    # Stage 2 — ask that persona. A failure here is reported separately from a
    # stage-1 failure on purpose: "no expert exists for this domain" and "the
    # expert exists but no provider could answer" are different outages with
    # different fixes, and merging them is how a budget ceiling reads as a
    # missing capability.
    try:
        pq = _backend("ace.persona_query")
        opinion = (pq.query_persona(sme.role_id, q, context=ctx.domain or "") or "").strip()
    except Exception as exc:
        logger.warning("Cortex sme backend persona query failed: %s", exc)
        return BackendResults(
            [],
            errors=[{
                "backend": "sme",
                "stage": "persona_query",
                "message": str(exc),
                "role_id": sme.role_id,
            }],
        )

    if not opinion:
        return BackendResults(
            [],
            errors=[{
                "backend": "sme",
                "stage": "persona_query",
                "message": f"persona {sme.role_id!r} returned an empty opinion",
                "role_id": sme.role_id,
            }],
        )

    return [
        CortexSearchResult(
            content=opinion,
            score=_SME_SCORE,
            backend="sme",
            strategy="persona_opinion",
            # The citation names WHO said it, not what it is evidence for —
            # which is the honest provenance for an opinion. source_table is
            # empty because no row backs it; the persona's two on-disk halves
            # are in metadata.
            citation=Citation(
                source_id=sme.role_id,
                source_type="sme_opinion",
                source_table="",
                title=sme.role_id.replace("_", " ").title(),
                snippet=opinion[:_SNIPPET_CHARS],
                url=f"/ace/roles/{sme.role_id}" if sme.role_id else "",
                classification=ctx.classification or "CUI",
            ),
            raw_scores={"scored": False},
            metadata={
                "advisory": True,
                "verdict_eligible": False,
                "role_id": sme.role_id,
                "sme_status": sme.status,
                "domain_label": sme.domain_label,
                "capability_bundle": sme.capability_bundle,
                "matched_existing": sme.matched_existing,
                "role_yaml_path": sme.role_yaml_path,
                "soul_path": sme.soul_path,
                "tenant_id": ctx.tenant_id,
            },
        )
    ]


# Dispatch table for the Cortex facade — keys match CORTEX_BACKENDS.
BACKEND_ADAPTERS = {
    "rag": search_rag,
    "graph": search_graph,
    "dic": search_dic,
    "kb": search_kb,
    "currency": search_currency,
    "sme": search_sme,
}


def search_all(
    query: str,
    top_k: int = 5,
    ctx: Optional[CortexContext] = None,
    backends: Optional[list] = None,
) -> list[CortexSearchResult]:
    """Run the requested backends and merge results.

    The default is EVIDENTIARY_BACKENDS, not CORTEX_BACKENDS: an advisory
    backend costs an LLM call and returns an opinion, so it is named explicitly
    (``backends=["sme"]``) or not run at all.

    Returns the combined list sorted by normalized score descending, as a
    :class:`BackendResults` whose ``.errors`` names the backends that failed.
    Each adapter is already exception-isolated, so one failing backend degrades
    to zero results without affecting the others.
    """
    merged: list[CortexSearchResult] = []
    errors: list[dict] = []
    for name in backends or EVIDENTIARY_BACKENDS:
        adapter = BACKEND_ADAPTERS.get(name)
        if adapter is None:
            logger.warning("Cortex search: unknown backend %r skipped", name)
            continue
        backend_results = adapter(query, top_k=top_k, ctx=ctx)
        merged.extend(backend_results)
        errors.extend(getattr(backend_results, "errors", ()) or ())
    return BackendResults(
        _rrf_fuse(merged, weights=resolve_strategy_weights()), errors=errors
    )


# ---------------------------------------------------------------------------
# Strategy router (ctx-search-02) — query classification -> backend selection
# ---------------------------------------------------------------------------

# Valid values for the ``strategy=`` override on search().
CORTEX_STRATEGIES = ("auto", "all") + CORTEX_BACKENDS

# Routing labels the classifier can emit and the backends each one selects.
# "ambiguous" fans out to search.fan_out.backends from args/cortex_config.yaml.
#
# `sme` has NO label here, deliberately. The classifier answers "what shape is
# this query", and no query shape means "consult an expert instead of the
# corpus" — that judgement belongs to whatever forms the verdict, once the
# deterministic backends have come back silent or in conflict. Giving it a label
# would make the advisory rung fire on a query pattern, i.e. automatically,
# which is exactly what ADVISORY_BACKENDS exists to prevent.
ROUTE_LABEL_BACKENDS = {
    "relational": ["graph"],
    "document": ["dic"],
    "factual": ["rag"],
    "exact_term": ["kb"],
    "currency": ["currency"],
}

# `sme` gets a far larger budget than the retrieval backends because it may pay
# for TWO LLM calls on a cold domain (persona generation, then the opinion),
# where the others do one DB/vector round trip.
_DEFAULT_TIMEOUTS = {
    "default": 10.0, "rag": 10.0, "graph": 8.0, "dic": 10.0, "kb": 5.0,
    # Two indexed LIKE reads, no embedding call and no model call.
    "currency": 5.0,
    "sme": 60.0,
}
# `currency` (cef-bck-01) IS in the automatic fan-out: it retrieves rows that
# existed before the query. `sme` (cef-bck-03) is deliberately absent and must
# stay absent — it is advisory, and fan-out is the automatic path, so adding it
# here would put an LLM opinion into the result set of every ambiguous query.
_DEFAULT_FAN_OUT_BACKENDS = ["rag", "graph", "dic", "currency"]
_DEFAULT_FACTUAL_CONFIDENCE = 0.75

# Exact-term / identifier lookups -> keyword KB. Quoted phrases, snake_case
# identifiers, dotted module paths, CVE ids, hex constants, vendor error codes.
_EXACT_TERM_PATTERNS = re.compile(
    r"(\"[^\"]{2,}\""
    r"|\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b"
    r"|\b[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z_][A-Za-z0-9_]*){2,}\b"
    r"|\bCVE-\d{4}-\d{4,}\b"
    r"|\b0x[0-9a-fA-F]{4,}\b"
    r"|\b[A-Z]{2,5}\d{3,}\b"
    r")"
)

# Relational / entity-traversal queries -> knowledge graph.
_RELATIONAL_PATTERNS = re.compile(
    r"\b(relationships?|related to|relates? to|connect(?:s|ed|ions?) (?:to|of|between)|"
    r"depends? on|dependenc(?:y|ies)|linked to|links? between|"
    r"neighbou?rs? of|topology|upstream|downstream|"
    r"who (?:owns|manages|maintains))\b",
    re.IGNORECASE,
)

# Document / clearance-scoped queries -> DIC grounded search.
_DOCUMENT_PATTERNS = re.compile(
    r"\b(documents?|policy|policies|sops?|sow|rfp|rfi|contract|report|manual|memo|"
    r"attachments?|appendix|section|page|clause|paragraph|"
    r"clearance|classified|cleared)\b",
    re.IGNORECASE,
)

# Currency / lifecycle queries -> the entity-currency backend. Checked AFTER
# the three older pattern rules so no query that routes somewhere today changes
# route: this rule only claims questions nothing else was claiming.
_CURRENCY_PATTERNS = re.compile(
    r"\b(end[- ]of[- ](?:life|support|sale|service)|eol|eos|eosl|"
    r"deprecat(?:ed|es|ion)|obsolete|superseded|retired|sunset|"
    r"still (?:current|supported|in support|valid|good|ok)|"
    r"out of support|no longer supported|"
    r"current(?:ly)? (?:approved|supported|standard)|"
    r"supported (?:until|through)|refresh cycle|tech(?:nology)? refresh)\b",
    re.IGNORECASE,
)


def _taxonomy_label(query: str) -> dict:
    """Label a query via the RAG query classifier (D-RAG-24 taxonomy).

    Failure degrades to confidence 0.0, which classify_route() treats as
    ambiguous (fan-out) rather than trusting a guessed label.
    """
    try:
        qc = _backend("rag.query_classifier")
        result = qc.classify_query(query) or {}
        return {
            "label": str(result.get("label") or ""),
            "confidence": float(result.get("confidence") or 0.0),
            "method": str(result.get("method") or ""),
        }
    except Exception as exc:
        logger.warning("Cortex router taxonomy classification failed: %s", exc)
        return {"label": "", "confidence": 0.0, "method": "error"}


def classify_route(query: str, config: Optional[dict] = None) -> dict:
    """Classify a query into a routing label and the backends to run.

    Deterministic pattern rules are checked first (exact_term -> kb,
    relational -> graph, document -> dic); only pattern-free queries consult
    the RAG taxonomy classifier: a confident ``fact_single`` routes to rag,
    everything else is ambiguous and fans out to the configured backend set.

    Returns ``{label, backends, method, reason}`` (+ ``taxonomy`` when the
    classifier was consulted).
    """
    cfg = config if config is not None else load_cortex_config()
    search_cfg = cfg.get("search") or {}
    fan_out = list(
        (search_cfg.get("fan_out") or {}).get("backends")
        or _DEFAULT_FAN_OUT_BACKENDS
    )
    q = (query or "").strip()
    if not q:
        return {
            "label": "ambiguous",
            "backends": fan_out,
            "method": "default",
            "reason": "Empty query — fan-out to default backends.",
        }
    if _EXACT_TERM_PATTERNS.search(q):
        return {
            "label": "exact_term",
            "backends": list(ROUTE_LABEL_BACKENDS["exact_term"]),
            "method": "pattern",
            "reason": "Query contains an exact term/identifier (quoted phrase, "
            "snake_case, dotted path, CVE/error code).",
        }
    if _RELATIONAL_PATTERNS.search(q):
        return {
            "label": "relational",
            "backends": list(ROUTE_LABEL_BACKENDS["relational"]),
            "method": "pattern",
            "reason": "Query asks about entity relationships/connections.",
        }
    if _DOCUMENT_PATTERNS.search(q):
        return {
            "label": "document",
            "backends": list(ROUTE_LABEL_BACKENDS["document"]),
            "method": "pattern",
            "reason": "Query targets documents or clearance-scoped content.",
        }
    if _CURRENCY_PATTERNS.search(q):
        return {
            "label": "currency",
            "backends": list(ROUTE_LABEL_BACKENDS["currency"]),
            "method": "pattern",
            "reason": "Query asks whether an entity is still current "
            "(lifecycle / EOL / deprecation).",
        }
    taxonomy = _taxonomy_label(q)
    factual_confidence = float(
        (search_cfg.get("router") or {}).get(
            "factual_confidence", _DEFAULT_FACTUAL_CONFIDENCE
        )
    )
    if (
        taxonomy["label"] == "fact_single"
        and taxonomy["confidence"] >= factual_confidence
    ):
        return {
            "label": "factual",
            "backends": list(ROUTE_LABEL_BACKENDS["factual"]),
            "method": f"taxonomy:{taxonomy['method']}",
            "reason": "Taxonomy classifier labeled the query fact_single with "
            f"confidence {taxonomy['confidence']:.2f}.",
            "taxonomy": taxonomy,
        }
    return {
        "label": "ambiguous",
        "backends": fan_out,
        "method": f"taxonomy:{taxonomy['method']}",
        "reason": "No routing pattern matched and taxonomy label "
        f"{taxonomy['label'] or 'unknown'!r} (confidence "
        f"{taxonomy['confidence']:.2f}) is not confidently factual — fan-out.",
        "taxonomy": taxonomy,
    }


def _run_backends(
    query: str,
    top_k: int,
    ctx: Optional[CortexContext],
    backends: list,
    search_cfg: dict,
) -> tuple:
    """Run backends concurrently with per-backend timeouts.

    Returns ``(results, ran, timed_out)``. A backend that exceeds its timeout
    is abandoned (its worker thread is not joined) and logged; the results
    from the backends that finished are still returned — partial results beat
    no results.

    ``results`` is a :class:`BackendResults`: any failure an adapter reported,
    plus every timeout, is carried on ``.errors`` so an EMPTY result set can
    still say why it is empty.
    """
    valid = []
    for name in backends:
        if name in BACKEND_ADAPTERS:
            valid.append(name)
        else:
            logger.warning("Cortex search: unknown backend %r skipped", name)
    if not valid:
        return BackendResults(), [], []

    timeouts = {**_DEFAULT_TIMEOUTS, **(search_cfg.get("timeouts") or {})}
    default_timeout = float(timeouts.get("default", 10.0))
    merged: list[CortexSearchResult] = []
    timed_out: list = []
    errors: list[dict] = []
    # Submit onto the shared, bounded pool — never create/shut down a per-call
    # executor (that leaked a thread per timed-out backend). A timed-out future
    # is abandoned; its worker is reused by the pool once the adapter returns.
    executor = _get_search_executor(search_cfg)
    start = time.monotonic()
    futures = {
        name: executor.submit(
            BACKEND_ADAPTERS[name], query, top_k=top_k, ctx=ctx
        )
        for name in valid
    }
    for name, future in futures.items():
        budget = float(timeouts.get(name, default_timeout))
        remaining = max(0.0, start + budget - time.monotonic())
        try:
            backend_results = future.result(timeout=remaining)
            merged.extend(backend_results)
            errors.extend(getattr(backend_results, "errors", ()) or ())
        except concurrent.futures.TimeoutError:
            future.cancel()
            timed_out.append(name)
            errors.append(
                {
                    "backend": name,
                    "stage": "timeout",
                    "message": f"timed out after {budget:.1f}s",
                }
            )
            logger.warning(
                "Cortex %s backend timed out after %.1fs — returning "
                "partial results",
                name,
                budget,
            )
    fused = _rrf_fuse(
        merged,
        k=int(search_cfg.get("rrf_k") or _DEFAULT_RRF_K),
        weights=resolve_strategy_weights(search_cfg),
    )
    return BackendResults(fused, errors=errors), valid, timed_out


def _routed_pass(
    query: str,
    top_k: int,
    ctx: CortexContext,
    strategy: str,
    cfg: dict,
    search_cfg: dict,
) -> list[CortexSearchResult]:
    """One route -> domain-scope -> fan-out -> stamp pass over the backends.

    Returns results sorted by score descending, each stamped with the routing
    decision (``result.strategy`` tag + ``metadata["router"]``). search()
    runs this once, and a second time on the rewritten query when the CRAG
    corrective loop triggers.
    """
    route: Optional[dict] = None
    if strategy == "auto":
        route = classify_route(query, config=cfg)
        label = route["label"]
        backends = list(route["backends"])
    elif strategy == "all":
        label = "override"
        # "all" means every backend that RETRIEVES. An advisory backend is not
        # part of "all" — a caller asking to search everything is asking for
        # evidence, not for an LLM to be consulted on their behalf.
        backends = list(EVIDENTIARY_BACKENDS)
    else:
        label = "override"
        backends = [strategy]

    domain_scope: dict = {}
    domain_sources: list = []
    if ctx.domain:
        domain_cfg = (search_cfg.get("domains") or {}).get(ctx.domain) or {}
        allowed = domain_cfg.get("backends") or []
        if allowed:
            scoped = [b for b in backends if b in allowed]
            backends = scoped or [b for b in allowed if b in BACKEND_ADAPTERS]
        domain_sources = list(domain_cfg.get("sources") or [])
        domain_scope = {
            "domain": ctx.domain,
            "collections": list(domain_cfg.get("collections") or []),
        }

    results, ran, timed_out = _run_backends(query, top_k, ctx, backends, search_cfg)

    # Row-level domain scope (ctx-canvas-04): drop hits whose source table/id
    # falls outside the domain's allowed source prefixes so security mode
    # returns only threat/vuln/incident evidence. No-op when the domain
    # declares no `sources` (general behavior). The drop count is recorded in
    # metadata so the scoping is observable, not silent.
    if domain_scope and domain_sources:
        from .domains import filter_by_sources

        kept, dropped = filter_by_sources(results, domain_sources)
        # Re-wrap: the filter returns a plain list, and dropping the backend
        # failures here is exactly how an empty set loses its explanation.
        results = BackendResults(kept, errors=getattr(results, "errors", ()))
        domain_scope["sources"] = list(domain_sources)
        domain_scope["filtered_out"] = len(dropped)

    strategy_tag = f"{strategy}:{label}[{'+'.join(ran)}]"
    router_record = {
        "strategy": strategy,
        "label": label,
        "backends": ran,
        "timed_out": timed_out,
    }
    if route is not None:
        router_record["reason"] = route.get("reason", "")
        router_record["method"] = route.get("method", "")
    if domain_scope:
        router_record["domain_scope"] = domain_scope
    for r in results:
        r.metadata["backend_strategy"] = r.strategy
        r.metadata["router"] = dict(router_record)
        r.strategy = strategy_tag
    return results


# ---------------------------------------------------------------------------
# CRAG corrective loop (ctx-search-04) — low-confidence rewrite + re-retrieve
# ---------------------------------------------------------------------------

_REWRITE_SYSTEM_PROMPT = (
    "You rewrite search queries whose retrieval results scored poorly. "
    "Produce ONE alternative query that surfaces the same information with "
    "different phrasing: expand acronyms, add synonyms, drop filler words. "
    "Return ONLY the rewritten query text, nothing else."
)
_REWRITE_MAX_TOKENS = 128
_REWRITE_SNIPPET_COUNT = 3


def rewrite_query(
    query: str,
    results: list,
    ctx: Optional[CortexContext] = None,
) -> str:
    """Rewrite a low-confidence query via the ``cortex_search_rewrite`` chain.

    The routing chain keeps a local ollama-tier fallback (air-gap invariant
    from ctx-core-03), and air-gapped contexts are forced local-only through
    ``api._invoke``. Any unavailability (missing model, budget exceeded,
    provider error) raises — search() converts that into a
    ``corrective_skipped`` marker instead of failing the search.
    """
    # Late import: api runs assert_airgap_ready() at import time, and its
    # failure is exactly the "rewrite path unavailable" signal callers catch.
    from . import api

    context = ctx or CortexContext()
    snippets = "\n".join(
        f"- {r.content[:_SNIPPET_CHARS]}" for r in results[:_REWRITE_SNIPPET_COUNT]
    )
    prompt = (
        f"Original query: {query}\n"
        f"Best results so far (low relevance):\n{snippets or '- (none)'}\n\n"
        "Rewritten query:"
    )
    request = api._build_request(
        prompt,
        context,
        system_prompt=_REWRITE_SYSTEM_PROMPT,
        max_tokens=_REWRITE_MAX_TOKENS,
        temperature=0.0,
    )
    response = api._invoke(api.CORTEX_SEARCH_REWRITE_FUNCTION, request, context)
    rewritten = (response.content or "").strip().splitlines()
    return rewritten[0].strip().strip('"') if rewritten else ""


def _merge_corrective(
    original: list[CortexSearchResult],
    corrective: list[CortexSearchResult],
    search_cfg: Optional[dict] = None,
) -> list[CortexSearchResult]:
    """Fuse both passes: dedupe by (backend, source_id), best raw score wins,
    then re-rank the survivors by RRF so an item found in both passes (and by
    multiple backends) rises.

    Takes the same ``search_cfg`` the fan-out used so the corrective merge
    ranks under the configured ``rrf_k`` + ``strategy_weights`` rather than a
    second, unconfigured RRF."""
    search_cfg = search_cfg or {}
    best: dict = {}
    for r in original + corrective:
        key = (r.backend, r.citation.source_id, r.content)
        if key not in best or r.score > best[key].score:
            best[key] = r
    return _rrf_fuse(
        list(best.values()),
        k=int(search_cfg.get("rrf_k") or _DEFAULT_RRF_K),
        weights=resolve_strategy_weights(search_cfg),
    )


def _corrective_pass(
    query: str,
    results: list[CortexSearchResult],
    top_k: int,
    ctx: CortexContext,
    strategy: str,
    cfg: dict,
    search_cfg: dict,
) -> list[CortexSearchResult]:
    """CRAG corrective loop: rewrite + re-retrieve once when confidence is low.

    Evaluator: the fused top score against ``search.crag_threshold`` from
    args/cortex_config.yaml. A missing/zero threshold disables the loop
    entirely. At most ONE corrective iteration runs (latency stays bounded by
    the same per-backend timeouts), and the outcome is always observable in
    result metadata: ``corrective_pass=True`` + a ``crag`` record when the
    re-retrieve ran, ``corrective_skipped=<reason>`` when the rewrite path
    was unavailable or produced nothing new.
    """
    threshold = float(search_cfg.get("crag_threshold") or 0.0)
    if threshold <= 0.0:
        return results
    # Best raw confidence across the fused set. results[0] is now the top-RRF
    # item, not necessarily the max-raw-score one, so take the max explicitly to
    # keep the CRAG trigger keyed on our best backend confidence.
    #
    # Advisory results are excluded (cef-bck-03): CRAG's evaluator asks "did
    # RETRIEVAL do well enough", and an opinion is evidence of neither outcome.
    # Its 0.0 would answer "retrieval failed", so an sme-only search would pay
    # for a query rewrite plus a whole second advisory pass — two more LLM calls
    # to correct a retrieval that never ran.
    # ``results and not evidentiary`` — NOT ``not evidentiary``. An EMPTY first
    # pass has no evidentiary results either, and that case must still correct:
    # "retrieval found nothing" is the strongest reason there is to rewrite the
    # query. Only a set that is non-empty AND entirely advisory is skipped.
    evidentiary = [r for r in results if not is_advisory(r)]
    if results and not evidentiary:
        return results
    top_score = max((r.score for r in evidentiary), default=0.0)
    if top_score >= threshold:
        return results

    crag_record = {
        "threshold": threshold,
        "original_top_score": round(top_score, 6),
        "original_query": query,
    }

    def _skip(reason: str) -> list[CortexSearchResult]:
        logger.info("Cortex CRAG corrective pass skipped: %s", reason)
        for r in results:
            r.metadata["corrective_skipped"] = reason
            r.metadata["crag"] = dict(crag_record)
        return results

    try:
        rewritten = rewrite_query(query, results, ctx)
    except Exception as exc:
        return _skip(f"rewrite unavailable: {exc}")
    if not rewritten or rewritten.strip().lower() == (query or "").strip().lower():
        return _skip("rewrite produced no usable new query")

    crag_record["rewritten_query"] = rewritten
    logger.info(
        "Cortex CRAG corrective pass: top score %.3f < %.2f — re-retrieving "
        "with rewritten query %r",
        top_score,
        threshold,
        rewritten,
    )
    corrective = _routed_pass(rewritten, top_k, ctx, strategy, cfg, search_cfg)
    merged = BackendResults(
        _merge_corrective(results, corrective, search_cfg),
        errors=[
            *(getattr(results, "errors", ()) or ()),
            *(getattr(corrective, "errors", ()) or ()),
        ],
    )
    for r in merged:
        r.metadata["corrective_pass"] = True
        r.metadata["crag"] = dict(crag_record)
    return merged


def search(
    query: str,
    top_k: int = 5,
    ctx: Optional[CortexContext] = None,
    strategy: str = "auto",
    config: Optional[dict] = None,
) -> list[CortexSearchResult]:
    """Unified Cortex search with agentic strategy routing + CRAG correction.

    ``strategy`` is one of CORTEX_STRATEGIES: ``"auto"`` classifies the query
    via classify_route(); a backend name (``"rag"``/``"graph"``/``"dic"``/
    ``"kb"``) or ``"all"`` bypasses classification entirely. ``ctx.domain``
    activates a domain lens (:mod:`tools.cortex.domains`): the backend
    selection is intersected with that domain's allowed backends from
    ``search.domains`` in args/cortex_config.yaml, and — when the domain
    declares ``sources`` — hits whose source table/id fall outside those
    prefixes are dropped (row-level scope). The domain's collection scope,
    source prefixes, and drop count are recorded in
    ``metadata["router"]["domain_scope"]``.

    Every result records the routing decision: ``result.strategy`` becomes
    ``"<strategy>:<label>[backend+backend]"`` (the adapter's native strategy
    moves to ``metadata["backend_strategy"]``), and ``metadata["router"]``
    carries the full decision including any timed-out backends. ``top_k``
    applies per backend, so fan-out can return more than ``top_k`` results.

    When the top fused score falls below ``search.crag_threshold``, one
    corrective iteration rewrites the query (routing function
    ``cortex_search_rewrite``) and re-runs routing+fusion; see
    _corrective_pass() for the metadata contract.

    The return is a :class:`BackendResults` — a list of hits that ALSO carries
    ``.errors``, the backends that failed or timed out. An empty list with a
    non-empty ``.errors`` means retrieval broke, not that nothing matched; a
    caller that renders an answer must not report the two the same way.
    """
    ctx = ctx or CortexContext()
    cfg = config if config is not None else load_cortex_config()
    search_cfg = cfg.get("search") or {}
    strategy = (strategy or "auto").lower()
    if strategy not in CORTEX_STRATEGIES:
        raise ValueError(
            f"Unknown Cortex search strategy {strategy!r}; "
            f"expected one of {CORTEX_STRATEGIES}"
        )

    results = _routed_pass(query, top_k, ctx, strategy, cfg, search_cfg)
    return _corrective_pass(query, results, top_k, ctx, strategy, cfg, search_cfg)
