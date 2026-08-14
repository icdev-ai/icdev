# CUI // SP-CTI
"""Self-RAG per-document reflective reranking (agx-rag-02).

The current reranker (``tools/rag/reranker.py``) blends everything into ONE
opaque score, conflating *relevance* with *sufficiency*. This module scores each
candidate document along three SEPARATE axes and composes the final ordering in
Python, so the retrieval decision is inspectable and the citation layer gets a
real ``supports`` signal to consume (feeding agx-verify-01 / CoVe directly):

    RELEVANT  — is the document on-topic for the query?
    SUPPORTS  — does it actually support the drafted claim? (N/A without a claim)
    USEFUL    — does it carry information the answer would use?

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). Pattern only; no upstream code vendored.

Deterministic-picker: the LLM commits only to a 3-value enum per axis
({yes, partial, no}); Python (:func:`compose_reflection_score`) composes the
number and the ordering, unit-tested against a truth table. Malformed output
fails safe to ``partial`` (neutral), never silently ``yes``.

Boundary with Corrective-RAG: ``tools/rag/corrective_rag.py`` is parallel
multi-strategy RETRIEVAL and deliberately does no per-document grading. The
grading half lives HERE (a reranking concern), not there — the two are not
duplicated. corrective_rag can consume this reranker's ``supports`` axis if a
doc-grading-with-fallback loop is added later.

Cost: per-document LLM calls are expensive at top-50, so reflection is bounded
to the POST-VECTOR candidate set the caller passes (already ≤ vector_top_k) and
routed cheap-tier. If it does not beat the current reranker per dollar on a
held-out set, the honest recommendation is to leave it disabled — a negative
result is a valid outcome (see :func:`ab_compare`).

LLM-agnostic: inference via ``LLMRouter`` only; no vendor SDK imports, no
hardcoded model IDs. Opt-in via ``args/rag_config.yaml`` ``rag.reflective_rerank``
(default disabled = current behavior).
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.rag.reflective_reranker")

VOCABULARY_VERSION = "reflect-1.0"
AXIS_VALUES = ("yes", "partial", "no")
_AXIS_MAP = {"yes": 1.0, "partial": 0.5, "no": 0.0}

# Axis weights when a claim IS provided (SUPPORTS is meaningful).
_W_WITH_CLAIM = {"relevant": 0.4, "supports": 0.4, "useful": 0.2}
# Axis weights when NO claim is provided (SUPPORTS is N/A, renormalized away).
_W_NO_CLAIM = {"relevant": 0.7, "useful": 0.3}


def map_axis(token: Any) -> float:
    """Map an axis enum to its float; unknown/malformed -> 0.5 (neutral)."""
    return _AXIS_MAP.get(str(token or "").strip().lower(), 0.5)


def compose_reflection_score(
    relevant: str,
    useful: str,
    *,
    supports: Optional[str] = None,
) -> float:
    """Compose a [0,1] reranking score from per-axis enums (pure function).

    When ``supports`` is None (no claim to check) the SUPPORTS axis is dropped
    and the weights renormalize to relevance/usefulness — documented policy so
    the ordering is reproducible and inspectable.
    """
    r = map_axis(relevant)
    u = map_axis(useful)
    if supports is None:
        score = _W_NO_CLAIM["relevant"] * r + _W_NO_CLAIM["useful"] * u
    else:
        s = map_axis(supports)
        score = (
            _W_WITH_CLAIM["relevant"] * r
            + _W_WITH_CLAIM["supports"] * s
            + _W_WITH_CLAIM["useful"] * u
        )
    return round(max(0.0, min(1.0, score)), 4)


def reflect_document(
    query: str,
    document: str,
    *,
    claim: Optional[str] = None,
    router=None,
    reflect_function: str = "rag_rerank",
) -> dict:
    """Return per-axis enum verdicts for one document (one cheap-tier call).

    Returns ``{relevant, useful, supports?, score, vocabulary_version}``. On any
    LLM/parse failure every axis degrades to ``partial`` (neutral) — the
    deterministic fallback, never a silent ``yes``.

    The shape is held to the shared contract validator (trust-struct-01) with
    ``partial`` as each axis's DECLARED fail-closed sentinel, so the neutral
    degrade is stated in one place instead of being an implicit consequence of
    "token not in AXIS_VALUES, leave the default".
    """
    from tools.quality.structured_output import (
        OutputContract,
        coerce_or_reject,
        enum_field,
    )

    axes = {"relevant": "partial", "useful": "partial"}
    if claim is not None:
        axes["supports"] = "partial"
    contract = OutputContract(
        {
            "type": "object",
            "required": sorted(axes),
            "properties": {
                axis: enum_field(AXIS_VALUES, fail_closed="partial") for axis in axes
            },
        },
        name="reflective_reranker.reflect_document",
    )
    try:
        if router is None:
            from tools.llm.router import LLMRouter
            router = LLMRouter()
        from tools.llm.provider import LLMRequest
        claim_line = f"\nCLAIM (to be supported): {claim}" if claim is not None else ""
        supports_schema = ', "supports": "yes"|"partial"|"no"' if claim is not None else ""
        prompt = (
            "Judge the DOCUMENT for answering the QUERY. For each axis choose "
            "EXACTLY one of yes | partial | no:\n"
            "  relevant: is the document on-topic for the query?\n"
            + ("  supports: does it support the CLAIM?\n" if claim is not None else "")
            + "  useful: does it carry information the answer would use?\n\n"
            'Respond STRICT JSON only: {"relevant": "yes"|"partial"|"no", '
            '"useful": "yes"|"partial"|"no"' + supports_schema + "}\n\n"
            f"QUERY: {query}{claim_line}\n\nDOCUMENT:\n{document[:1500]}"
        )
        resp = router.invoke(reflect_function, LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60, temperature=0.0,
        ))
        content = (getattr(resp, "content", "") or "").strip()
        # coerce: every axis declares "partial" as its sentinel, so an unknown
        # token, a wrong type, or an absent axis all land on neutral — and each
        # one is a recorded finding rather than a silently-kept default. An
        # unparseable payload is a rejection; the neutral axes below stand.
        data, findings = coerce_or_reject(content, contract, mode="coerce")
        if data is None:
            logger.debug(
                "reflective_reranker: output rejected (%s), neutral used",
                ",".join(sorted({f["code"] for f in findings})),
            )
        else:
            for axis in list(axes):
                axes[axis] = str(data[axis]).strip().lower()
    except Exception as exc:  # noqa: BLE001 — neutral fallback is the floor
        logger.debug("reflective_reranker: reflect failed, neutral used: %s", exc)

    score = compose_reflection_score(
        axes["relevant"], axes["useful"], supports=axes.get("supports")
    )
    return {**axes, "score": score, "vocabulary_version": VOCABULARY_VERSION}


def reflective_rerank(
    query: str,
    results: List[Any],
    *,
    claim: Optional[str] = None,
    top_k: int = 5,
    router=None,
    reflect_function: str = "rag_rerank",
    max_candidates: Optional[int] = None,
) -> List[Any]:
    """Rerank candidates by composed reflection score (Python-composed ordering).

    ``results`` are SearchResult-like objects with a ``content`` attribute (or
    dicts with ``content``). Reflection is bounded to the first
    ``max_candidates`` (default: all passed, which is already the post-vector
    set). Each result is annotated with ``reflection`` (the per-axis verdicts)
    so the citation layer can read the ``supports`` signal. Returns top_k.
    """
    if not results:
        return []
    limit = max_candidates or len(results)
    scored = []
    for i, r in enumerate(results):
        content = getattr(r, "content", None)
        if content is None and isinstance(r, dict):
            content = r.get("content", "")
        if i < limit:
            reflection = reflect_document(
                query, str(content or ""), claim=claim, router=router,
                reflect_function=reflect_function,
            )
        else:
            # Beyond the bounded set: keep but score neutral (no LLM spend).
            reflection = {"relevant": "partial", "useful": "partial", "score": 0.5,
                          "vocabulary_version": VOCABULARY_VERSION}
        # Attach for downstream (citation/CoVe) consumption; don't fabricate.
        try:
            setattr(r, "reflection", reflection)
            setattr(r, "rerank_score", reflection["score"])
        except Exception:  # dict / immutable — carry alongside
            pass
        scored.append((reflection["score"], i, r))
    # Stable sort: score desc, original index asc for ties (reproducible).
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in scored[:top_k]]


def _relevant_at_k(ranked: List[Any], gold_relevant_ids: set, k: int, id_of: Callable) -> float:
    """Precision@k against a gold-relevant id set."""
    if k <= 0:
        return 0.0
    topk = ranked[:k]
    hits = sum(1 for r in topk if id_of(r) in gold_relevant_ids)
    return round(hits / min(k, len(topk)) if topk else 0.0, 4)


def ab_compare(
    labeled_queries: List[dict],
    *,
    baseline_rank: Callable[[str, List[Any]], List[Any]],
    reflective_rank: Callable[[str, List[Any]], List[Any]],
    id_of: Callable[[Any], Any] = lambda r: getattr(r, "id", None),
    k: int = 5,
) -> dict:
    """Deterministic A/B harness: baseline reranker vs reflective reranker.

    ``labeled_queries``: ``[{"query", "candidates":[...], "gold_relevant":[ids],
    "baseline_calls":int, "reflective_calls":int}]``. Reports precision@k for
    each ranker plus the LLM-call cost proxy, so a reviewer can see quality AND
    cost. An honest negative result (reflective not worth the cost) is a valid
    conclusion. Live cross-model numbers are graded by agx-bench-01.
    """
    base_p, refl_p = [], []
    base_calls = refl_calls = 0
    for item in labeled_queries:
        q = item["query"]
        cands = item["candidates"]
        gold = set(item.get("gold_relevant", []))
        base_p.append(_relevant_at_k(baseline_rank(q, cands), gold, k, id_of))
        refl_p.append(_relevant_at_k(reflective_rank(q, cands), gold, k, id_of))
        base_calls += int(item.get("baseline_calls", 0))
        refl_calls += int(item.get("reflective_calls", len(cands)))
    n = len(labeled_queries) or 1
    base_mean = round(sum(base_p) / n, 4)
    refl_mean = round(sum(refl_p) / n, 4)
    return {
        "n": len(labeled_queries),
        "baseline_precision_at_k": base_mean,
        "reflective_precision_at_k": refl_mean,
        "quality_delta": round(refl_mean - base_mean, 4),
        "baseline_llm_calls": base_calls,
        "reflective_llm_calls": refl_calls,
        "cost_multiplier": round(refl_calls / base_calls, 2) if base_calls else None,
        "recommendation": (
            "enable" if refl_mean > base_mean else "leave_disabled_negative_result"
        ),
        "vocabulary_version": VOCABULARY_VERSION,
    }
