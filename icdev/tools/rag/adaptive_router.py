# CUI // SP-CTI
"""Adaptive RAG complexity pre-routing (agx-rag-01).

Route the retrieval STRATEGY by assessed query complexity BEFORE retrieving,
instead of always running the full two-stage pipeline (vector top-50 -> BM25 ->
time-decay -> cross-encoder rerank -> top-5) regardless of whether the query
needed retrieval at all:

    none    -> skip retrieval entirely (trivial / self-answerable query)
    simple  -> single-pass RAGRetriever.search (the existing pipeline)
    complex -> decompose / multi-hop path (existing rag_server decompose, or an
               injected decompose callable; falls back to a widened single pass)

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). Pattern only; no upstream code vendored.

Deterministic-picker: the LLM commits only to a 3-value complexity ENUM; Python
(:func:`decide_route`) composes the routing decision. A 3-token vocabulary is
portable across model families and cheap-tier reliable, with a keyword heuristic
as the deterministic fallback when structured output is malformed.

SAFETY (enforced in code, not documented as a caveat): the ``none`` route
produces an answer with NO retrieved evidence, which collides with the TRUST
citation requirement. So ``none`` is UNAVAILABLE whenever the calling surface
requires citations — :func:`classify_complexity` clamps it to ``simple`` and
:func:`decide_route` refuses to emit ``none`` under ``requires_citations``. A
wrongly-skipped retrieval can never yield an uncited claim on a citation surface.

LLM-agnostic: inference via ``LLMRouter`` only; no vendor SDK imports, no
hardcoded model IDs. Opt-in via ``args/rag_config.yaml`` ``rag.adaptive_routing``
(default disabled = current behavior).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.rag.adaptive_router")

# The complexity vocabulary. Ordered least->most work.
COMPLEXITY_VALUES = ("none", "simple", "complex")
VOCABULARY_VERSION = "adaptive-1.0"

# Heuristic signals for the deterministic fallback (no LLM). Deliberately
# conservative: when unsure, prefer MORE retrieval (simple/complex), never less.
_COMPLEX_MARKERS = (
    "compare", "difference between", "trade-off", "tradeoff", "why", "how does",
    "relationship", "impact of", "across", "versus", " vs ", "multi", "each of",
    "list all", "and also", "as well as", "then", "root cause",
)
_TRIVIAL_MARKERS = (
    "hello", "hi ", "thanks", "thank you", "what is your name", "who are you",
)


def _heuristic_complexity(query: str) -> str:
    """Keyword/length heuristic complexity. Conservative: never returns 'none'
    for anything that looks like a real information request."""
    q = (query or "").strip().lower()
    if not q:
        return "none"
    words = q.split()
    if len(words) <= 3 and any(m in q for m in _TRIVIAL_MARKERS):
        return "none"
    if any(m in q for m in _COMPLEX_MARKERS) or len(words) > 28 or q.count("?") > 1:
        return "complex"
    return "simple"


def classify_complexity(
    query: str,
    *,
    router=None,
    requires_citations: bool = False,
    llm_function: str = "rag_complexity_classify",
    config: Optional[dict] = None,
) -> dict:
    """Classify query complexity into the enum, cheap-tier-routed.

    Returns ``{complexity, source, vocabulary_version}`` where ``source`` is
    ``"llm"`` or ``"heuristic"``. When ``requires_citations`` is True the result
    can never be ``none`` — it is clamped up to ``simple`` (the citation-safety
    invariant, enforced here).
    """
    heuristic = _heuristic_complexity(query)
    complexity = heuristic
    source = "heuristic"

    use_llm = True
    if isinstance(config, dict):
        use_llm = config.get("use_llm_classifier", True)

    if use_llm:
        try:
            if router is None:
                from tools.llm.router import LLMRouter
                router = LLMRouter()
            from tools.llm.provider import LLMRequest
            prompt = (
                "Classify how much retrieval this QUERY needs. Choose EXACTLY one "
                "label:\n"
                "  none    = trivial / answerable without any document lookup\n"
                "  simple  = a single fact lookup answers it\n"
                "  complex = needs multiple facts, comparison, or multi-hop reasoning\n\n"
                'Respond with STRICT JSON only: {"complexity": "none"|"simple"|"complex"}\n\n'
                f"QUERY: {query}"
            )
            resp = router.invoke(llm_function, LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=40, temperature=0.0,
            ))
            content = (getattr(resp, "content", "") or "").strip()
            match = re.search(r"\{.*\}", content, re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
            token = str(data.get("complexity", "")).strip().lower()
            if token in COMPLEXITY_VALUES:
                complexity = token
                source = "llm"
            # else: keep the heuristic (deterministic fallback for malformed output)
        except Exception as exc:  # noqa: BLE001 — heuristic is always the floor
            logger.debug("adaptive_router: LLM classify failed, heuristic used: %s", exc)

    # Citation-safety invariant: 'none' is unavailable on citation surfaces.
    if requires_citations and complexity == "none":
        complexity = "simple"
        source += "+citation_clamp"

    return {
        "complexity": complexity,
        "source": source,
        "vocabulary_version": VOCABULARY_VERSION,
    }


def decide_route(complexity: str, *, requires_citations: bool = False) -> str:
    """Pure Python composition of the routing decision from the complexity enum.

    Returns one of ``skip`` / ``single_pass`` / ``decompose``. Unknown tokens
    fail safe to ``single_pass`` (retrieve rather than skip). ``skip`` is never
    returned when ``requires_citations`` is set.
    """
    c = str(complexity or "").strip().lower()
    if c == "none" and not requires_citations:
        return "skip"
    if c == "complex":
        return "decompose"
    # simple, unknown, or none-under-citations -> retrieve single pass
    return "single_pass"


class AdaptiveRetriever:
    """Wrap the existing retriever with complexity pre-routing.

    Enabled only when ``rag.adaptive_routing.enabled`` is true in
    ``args/rag_config.yaml`` (default false = current behavior). When disabled,
    :meth:`retrieve` is byte-for-byte the single-pass path.
    """

    def __init__(self, *, retriever=None, router=None, config: Optional[dict] = None,
                 decompose_fn: Optional[Callable[..., List[Any]]] = None):
        self._retriever = retriever
        self._router = router
        self._decompose_fn = decompose_fn
        if config is None:
            from tools.rag.retriever import _load_rag_config
            config = _load_rag_config()
        self._config = config or {}
        self._cfg = (self._config.get("rag", {}) or {}).get("adaptive_routing", {}) or {}

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get("enabled", False))

    def _get_retriever(self):
        if self._retriever is None:
            from tools.rag.retriever import RAGRetriever
            self._retriever = RAGRetriever(config=self._config)
        return self._retriever

    def retrieve(self, query: str, *, requires_citations: bool = False, **kwargs) -> dict:
        """Route and retrieve. Returns
        ``{route, complexity, source, results, retrieved}``.

        ``retrieved`` is False only on the ``skip`` route (a real call was
        avoided) — the metric agx-bench-01 attributes cost savings to.
        """
        # Disabled -> current behavior: always single-pass, no classification.
        if not self.enabled:
            results = self._get_retriever().search(query, **kwargs)
            return {"route": "single_pass", "complexity": "disabled",
                    "source": "disabled", "results": results, "retrieved": True}

        cls = classify_complexity(
            query, router=self._router, requires_citations=requires_citations,
            config=self._cfg,
        )
        route = decide_route(cls["complexity"], requires_citations=requires_citations)

        if route == "skip":
            return {"route": "skip", "complexity": cls["complexity"],
                    "source": cls["source"], "results": [], "retrieved": False}
        if route == "decompose" and self._decompose_fn is not None:
            results = self._decompose_fn(query, **kwargs)
            return {"route": "decompose", "complexity": cls["complexity"],
                    "source": cls["source"], "results": results, "retrieved": True}
        # single_pass, or decompose without an injected decompose_fn -> widened
        # single pass (honest fallback; the decompose path is pluggable).
        if route == "decompose":
            complex_top_k = self._cfg.get("complex_top_k")
            if complex_top_k and "top_k" not in kwargs:
                kwargs["top_k"] = complex_top_k
        results = self._get_retriever().search(query, **kwargs)
        return {"route": route, "complexity": cls["complexity"],
                "source": cls["source"], "results": results, "retrieved": True}


def measure_savings(labeled_queries: List[dict], *, router=None, config: Optional[dict] = None) -> dict:
    """Deterministic measurement harness for the routing decision.

    ``labeled_queries`` is a list of ``{"query": str, "gold_route": str,
    "requires_citations": bool}``. Computes retrieval-calls-saved (skip rate)
    and routing accuracy against the gold labels using the SAME classify/decide
    path production uses. Ship these numbers, not a claim. Live cross-model
    quality deltas are graded by agx-bench-01.
    """
    total = len(labeled_queries)
    skipped = 0
    correct = 0
    confusion: dict = {}
    for item in labeled_queries:
        q = item.get("query", "")
        rc = bool(item.get("requires_citations", False))
        cls = classify_complexity(q, router=router, requires_citations=rc, config=config)
        route = decide_route(cls["complexity"], requires_citations=rc)
        gold = item.get("gold_route")
        if route == "skip":
            skipped += 1
        if gold is not None and route == gold:
            correct += 1
        key = f"{gold}->{route}"
        confusion[key] = confusion.get(key, 0) + 1
    return {
        "n": total,
        "retrieval_calls_saved": skipped,
        "skip_rate": round(skipped / total, 4) if total else 0.0,
        "routing_accuracy": round(correct / total, 4) if total else 0.0,
        "confusion": confusion,
        "vocabulary_version": VOCABULARY_VERSION,
    }
