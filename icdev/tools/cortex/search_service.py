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

On top of the adapters sits the strategy router (``search()``): queries are
classified (pattern rules + the RAG taxonomy classifier) and routed to the
right backend(s), with ambiguous queries fanning out in parallel under
per-backend timeouts from ``args/cortex_config.yaml``.

When more than one backend contributes hits, ``fuse_results`` merges the
per-backend rankings into one list via cross-backend Reciprocal Rank Fusion
(ctx-search-03): duplicates collapse by ``(citation.source_table,
citation.source_id)`` with merged provenance, a final rerank pass runs over
the fused top-N via ``tools/rag/reranker.py``, and scores are normalized to
[0, 1]. A single contributing backend passes through untouched.
"""
from __future__ import annotations

import concurrent.futures
import importlib
import logging
import re
import time
from pathlib import Path
from typing import Optional

from .schemas import CORTEX_BACKENDS, Citation, CortexContext, CortexSearchResult

logger = logging.getLogger(__name__)

# "tools" when loaded via the shim namespace, "icdev.tools" when canonical.
_NS = __name__.rsplit(".cortex.", 1)[0]

_SNIPPET_CHARS = 200

# Fusion fallback defaults — overridable from args/cortex_config.yaml under
# search.fusion.  Change config, not code.
_RRF_K_DEFAULT = 60  # RRF paper default k (score denominator)
_RERANK_TOP_N_DEFAULT = 10  # fused results forwarded to the final rerank pass


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
    """Run the requested backends (default: all four) and fuse the results.

    When two or more backends return hits, the per-backend rankings are
    fused via cross-backend RRF (``fuse_results``): duplicates collapse
    with merged provenance, a final rerank pass runs over the fused top-N,
    and scores are normalized to [0, 1]. A single contributing backend
    passes through untouched. Each adapter is already exception-isolated,
    so one failing backend degrades to zero results without affecting the
    others.
    """
    per_backend: list[list[CortexSearchResult]] = []
    for name in backends or CORTEX_BACKENDS:
        adapter = BACKEND_ADAPTERS.get(name)
        if adapter is None:
            logger.warning("Cortex search: unknown backend %r skipped", name)
            continue
        per_backend.append(adapter(query, top_k=top_k, ctx=ctx))
    return fuse_results(query, per_backend)


# ---------------------------------------------------------------------------
# Strategy router (ctx-search-02) — query classification -> backend selection
# ---------------------------------------------------------------------------

# Valid values for the ``strategy=`` override on search().
CORTEX_STRATEGIES = ("auto", "all") + CORTEX_BACKENDS

# Routing labels the classifier can emit and the backends each one selects.
# "ambiguous" fans out to search.fan_out.backends from args/cortex_config.yaml.
ROUTE_LABEL_BACKENDS = {
    "relational": ["graph"],
    "document": ["dic"],
    "factual": ["rag"],
    "exact_term": ["kb"],
}

_DEFAULT_TIMEOUTS = {"default": 10.0, "rag": 10.0, "graph": 8.0, "dic": 10.0, "kb": 5.0}
_DEFAULT_FAN_OUT_BACKENDS = ["rag", "graph", "dic"]
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

_config_cache: Optional[dict] = None


def _find_repo_root() -> Optional[Path]:
    """Walk up from this file until a directory containing ``args/`` is found.

    Works from both namespace roots (``tools/`` and ``icdev/tools/``) and from
    git worktrees; never consults ``os.getcwd()``.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "args").is_dir():
            return parent
    return None


def load_cortex_config(refresh: bool = False) -> dict:
    """Load and cache ``args/cortex_config.yaml``; missing file -> ``{}``."""
    global _config_cache
    if _config_cache is not None and not refresh:
        return _config_cache
    cfg: dict = {}
    root = _find_repo_root()
    path = root / "args" / "cortex_config.yaml" if root else None
    if path is not None and path.exists():
        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.warning("Cortex config load failed (%s): %s", path, exc)
    _config_cache = cfg
    return cfg


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

    Returns ``(per_backend, ran, timed_out)`` where ``per_backend`` is one
    ranked result list per backend that finished (fusion input). A backend
    that exceeds its timeout is abandoned (its worker thread is not joined)
    and logged; the lists from the backends that finished are still
    returned — partial results beat no results.
    """
    valid = []
    for name in backends:
        if name in BACKEND_ADAPTERS:
            valid.append(name)
        else:
            logger.warning("Cortex search: unknown backend %r skipped", name)
    if not valid:
        return [], [], []

    timeouts = {**_DEFAULT_TIMEOUTS, **(search_cfg.get("timeouts") or {})}
    default_timeout = float(timeouts.get("default", 10.0))
    max_workers = int(
        (search_cfg.get("fan_out") or {}).get("max_workers") or len(valid)
    )
    per_backend: list[list[CortexSearchResult]] = []
    timed_out: list = []
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(valid))),
        thread_name_prefix="cortex-search",
    )
    start = time.monotonic()
    try:
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
                per_backend.append(future.result(timeout=remaining) or [])
            except concurrent.futures.TimeoutError:
                future.cancel()
                timed_out.append(name)
                logger.warning(
                    "Cortex %s backend timed out after %.1fs — returning "
                    "partial results",
                    name,
                    budget,
                )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return per_backend, valid, timed_out


def search(
    query: str,
    top_k: int = 5,
    ctx: Optional[CortexContext] = None,
    strategy: str = "auto",
    config: Optional[dict] = None,
) -> list[CortexSearchResult]:
    """Unified Cortex search with agentic strategy routing.

    ``strategy`` is one of CORTEX_STRATEGIES: ``"auto"`` classifies the query
    via classify_route(); a backend name (``"rag"``/``"graph"``/``"dic"``/
    ``"kb"``) or ``"all"`` bypasses classification entirely. ``ctx.domain``
    intersects the selection with that domain's allowed backends from
    ``search.domains`` in args/cortex_config.yaml and records the domain's
    collection scope in metadata (consumption hook for ctx-canvas-04).

    Every result records the routing decision: ``result.strategy`` becomes
    ``"<strategy>:<label>[backend+backend]"`` (the adapter's native strategy
    moves to ``metadata["backend_strategy"]``), and ``metadata["router"]``
    carries the full decision including any timed-out backends. ``top_k``
    applies per backend, so fan-out can return more than ``top_k`` results.

    When more than one backend contributes hits, the per-backend rankings
    are fused via cross-backend RRF (``fuse_results``); a single
    contributing backend passes through untouched.
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

    route: Optional[dict] = None
    if strategy == "auto":
        route = classify_route(query, config=cfg)
        label = route["label"]
        backends = list(route["backends"])
    elif strategy == "all":
        label = "override"
        backends = list(CORTEX_BACKENDS)
    else:
        label = "override"
        backends = [strategy]

    domain_scope: dict = {}
    if ctx.domain:
        domain_cfg = (search_cfg.get("domains") or {}).get(ctx.domain) or {}
        allowed = domain_cfg.get("backends") or []
        if allowed:
            scoped = [b for b in backends if b in allowed]
            backends = scoped or [b for b in allowed if b in BACKEND_ADAPTERS]
        domain_scope = {
            "domain": ctx.domain,
            "collections": list(domain_cfg.get("collections") or []),
        }

    per_backend, ran, timed_out = _run_backends(query, top_k, ctx, backends, search_cfg)
    results = fuse_results(query, per_backend, config=cfg)

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
# Cross-backend RRF fusion (ctx-search-03)
# ---------------------------------------------------------------------------


def _dedupe_key(result: CortexSearchResult, fallback) -> tuple:
    """Provenance identity used for cross-backend dedupe.

    Results without a ``source_id`` have no usable provenance identity and
    are never merged with each other (keyed on ``fallback``, the object id).
    """
    source_id = result.citation.source_id if result.citation else ""
    if not source_id:
        return ("", fallback)
    return (result.citation.source_table, source_id)


def _rerank_fused(
    query: str,
    results: list[CortexSearchResult],
    top_n: int,
    fusion_cfg: dict,
) -> list[CortexSearchResult]:
    """Final rerank pass over the fused list via ``tools/rag/reranker.py``.

    The fused results are wrapped in RAG ``SearchResult`` stubs (chunk_id =
    fused index) so ``rerank_results`` can score them; reranked hits come
    back first with the blended score, hits the reranker did not return keep
    their fused score and order after it. Any failure keeps the fused
    ordering — the rerank pass can only refine, never lose results.
    """
    if top_n <= 0 or len(results) <= 1:
        return results
    try:
        reranker = _backend("rag.reranker")
        vsp = _backend("rag.vector_store_provider")
        stubs = [
            vsp.SearchResult(
                chunk_id=str(i),
                content=r.content,
                score=r.score,
                final_score=r.score,
            )
            for i, r in enumerate(results)
        ]
        ranked = reranker.rerank_results(
            query,
            stubs,
            top_k=min(top_n, len(results)),
            config=fusion_cfg.get("rerank") or {},
        )
        if not ranked:
            return results
        out: list[CortexSearchResult] = []
        used: set = set()
        for stub in ranked:
            try:
                idx = int(stub.chunk_id)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(results) or idx in used:
                continue
            used.add(idx)
            result = results[idx]
            rerank_score = getattr(stub, "rerank_score", 0.0)
            if rerank_score:
                result.raw_scores["fused_rerank"] = rerank_score
            result.score = _clamp(stub.final_score)
            out.append(result)
        for i, result in enumerate(results):
            if i not in used:
                out.append(result)
        return out
    except Exception as exc:
        logger.warning("Cortex rerank pass failed, keeping fused order: %s", exc)
        return results


def fuse_results(
    query: str,
    backend_results: list,
    rrf_k: Optional[int] = None,
    rerank: Optional[bool] = None,
    rerank_top_n: Optional[int] = None,
    config: Optional[dict] = None,
) -> list[CortexSearchResult]:
    """Fuse per-backend CortexSearchResult rankings into one list (RRF).

    Cross-backend variant of the intra-backend RRF in
    ``tools/rag/retriever.py``: each backend list is one ranking, and every
    appearance of a result contributes ``1 / (k + rank)`` to its fused
    score (``k`` from ``search.fusion.rrf_k`` in args/cortex_config.yaml,
    default 60). Duplicates by ``(citation.source_table,
    citation.source_id)`` collapse to one result — contributions sum, the
    best-ranked duplicate supplies content/citation, ``raw_scores`` merge
    (collisions namespaced as ``"<backend>:<key>"``), and
    ``metadata["fused_backends"]`` records which backends agreed.

    The fused RRF value is kept in ``raw_scores["rrf"]`` and the pre-rerank
    ordering in ``raw_scores["fused_rank"]`` (1-based); ``score`` is
    normalized to [0, 1]. When more than one backend contributed, a final
    rerank pass runs over the fused top-N (``_rerank_fused``). Ties are
    order-stable: first appearance (backend order, then rank) wins.

    A single contributing backend (or none) passes through untouched —
    no RRF, no rerank, no re-scoring.
    """
    contributing = [lst for lst in (backend_results or []) if lst]
    if not contributing:
        return []
    if len(contributing) == 1:
        return list(contributing[0])

    cfg = config if config is not None else load_cortex_config()
    fusion_cfg = (cfg.get("search") or {}).get("fusion") or {}
    k = rrf_k if rrf_k is not None else fusion_cfg.get("rrf_k", _RRF_K_DEFAULT)

    entries: dict = {}
    for lst in contributing:
        for rank, result in enumerate(lst, start=1):
            contribution = 1.0 / (k + rank)
            key = _dedupe_key(result, id(result))
            entry = entries.get(key)
            if entry is None:
                entries[key] = {
                    "result": result,
                    "fused": contribution,
                    "best": contribution,
                    "order": len(entries),
                    "backends": [result.backend],
                    "raw": dict(result.raw_scores or {}),
                }
                continue
            entry["fused"] += contribution
            if result.backend not in entry["backends"]:
                entry["backends"].append(result.backend)
            for name, value in (result.raw_scores or {}).items():
                if name in entry["raw"] and entry["raw"][name] != value:
                    entry["raw"][f"{result.backend}:{name}"] = value
                else:
                    entry["raw"].setdefault(name, value)
            if contribution > entry["best"]:
                entry["result"] = result
                entry["best"] = contribution

    # Stable for ties: equal fused scores keep first-appearance order.
    ordered = sorted(entries.values(), key=lambda e: (-e["fused"], e["order"]))

    peak = ordered[0]["fused"]
    fused: list[CortexSearchResult] = []
    for position, entry in enumerate(ordered, start=1):
        result = entry["result"]
        raw = entry["raw"]
        raw["rrf"] = entry["fused"]
        raw["fused_rank"] = position
        result.raw_scores = raw
        result.score = _clamp(entry["fused"] / peak)
        if len(entry["backends"]) > 1:
            result.metadata["fused_backends"] = list(entry["backends"])
        fused.append(result)

    do_rerank = (
        rerank if rerank is not None else bool(fusion_cfg.get("rerank_enabled", True))
    )
    if do_rerank:
        top_n = (
            rerank_top_n
            if rerank_top_n is not None
            else fusion_cfg.get("rerank_top_n", _RERANK_TOP_N_DEFAULT)
        )
        fused = _rerank_fused(query, fused, top_n=top_n, fusion_cfg=fusion_cfg)
        norm = max((r.score for r in fused), default=0.0)
        if 0.0 < norm < 1.0:
            for r in fused:
                r.score = _clamp(r.score / norm)
    return fused
