# CUI // SP-CTI
"""Grounding assessment for Migration Canvas LLM surfaces (cnr-mdc-03).

Thin adapter over the shared ``tools/quality/citation_grounding.py`` module —
this file does NOT re-implement citation parsing/validation, it composes the
shared primitives into a single verdict every Migration Canvas LLM surface can
attach to its response so callers/UI can surface a grounding warning.

TRUST invariant: LLM-drafted migration guidance must either carry validated
``[source: …]`` citations (grounded) or a low-confidence grounding-warning flag
so the reader knows the text is model-generated and unattributed.

Usage:
    from tools.migration_canvas.grounding import assess_response
    g = assess_response(text, allowed_sources=None, model=model_id)
    # g["grounding_warning"] is set when the text is ungrounded.
"""

from __future__ import annotations

from tools.quality.citation_grounding import (
    CONF_ABSTAIN,
    CONF_INCLUDE,
    classify_confidence,
    parse_citations,
    validate_citations,
)

# Confidence assigned to ungrounded (no-citation) model output — below the
# CONF_ABSTAIN band so classify_confidence() reports "abstain".
_UNGROUNDED_CONFIDENCE = 0.3

GROUNDING_WARNING = (
    "Model-generated migration guidance without cited sources — verify against "
    "authoritative migration references before acting."
)


def assess_response(
    text: str,
    allowed_sources=None,
    *,
    model: str = "",
    method: str = "",
) -> dict:
    """Return a grounding verdict for one LLM-drafted response.

    Args:
        text: the LLM-generated response text.
        allowed_sources: optional int count or iterable of valid source ids.
            When provided, citations outside this set are flagged as
            hallucinated. When None (no retrieval context — the common case for
            general migration advice), only citation *presence* is assessed.
        model: model id that produced the text (recorded for provenance).
        method: generation method label (recorded for provenance).

    Returns a dict with keys:
        grounded, has_citations, cited_sources, hallucinated_citations,
        confidence, confidence_band, grounding_warning, model, method.
    """
    text = text or ""
    cited = parse_citations(text)
    has = bool(cited)

    hallucinated: list[str] = []
    if allowed_sources is not None:
        hallucinated = validate_citations(text, allowed_sources)["hallucinated_citations"]

    if has and not hallucinated:
        confidence = CONF_INCLUDE
    elif has and hallucinated:
        # Cites sources that don't exist — worse than no citation.
        confidence = CONF_ABSTAIN
    else:
        confidence = _UNGROUNDED_CONFIDENCE

    band = classify_confidence(confidence)

    warning: str | None = None
    if not has:
        warning = GROUNDING_WARNING
    elif hallucinated:
        warning = f"Cited unavailable source(s): {', '.join(hallucinated)}"

    return {
        "grounded": has and not hallucinated,
        "has_citations": has,
        "cited_sources": cited,
        "hallucinated_citations": hallucinated,
        "confidence": confidence,
        "confidence_band": band,
        "grounding_warning": warning,
        "model": model,
        "method": method,
    }
