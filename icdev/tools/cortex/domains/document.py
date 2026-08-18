# CUI // SP-CTI
"""Document-intelligence domain lens — triage formatter.

Records/compliance framing over retrieved document hits: what the documents
say, then coverage, then gaps / required actions. Deterministic and grounded by
construction (every line derives from a real retrieved row).
"""
from __future__ import annotations

from typing import List

from ._triage import grounded_triage

DOCUMENT_DOMAIN = "document"

# The DIC-corpus-only lens (cef-bck-04). Same records/compliance framing as
# `document` — a triage over document hits is the same shape whether or not the
# hits were row-scoped to dic_ — so it reuses this module rather than adding a
# near-identical sibling. Only the reported `domain` label differs, and it has
# to: a brief that says "document" for a document_intelligence query misreports
# which lens produced it.
DOCUMENT_INTELLIGENCE_DOMAIN = "document_intelligence"

_HEADERS = {
    "title": "Document triage",
    "empty": "No document-scoped findings.",
    "evidence": "What the documents say (confidence-ranked, cited):",
    "coverage": "Document coverage",
    "actions": "Gaps / required actions:",
}


def _recommended_actions(ranked: list) -> List[str]:
    """Records/compliance actions derived from the retrieved rows."""
    if not ranked:
        return ["No documents retrieved — ingest or widen the collection scope."]
    actions: List[str] = []
    uncited = [
        r for r in ranked
        if not getattr(getattr(r, "citation", None), "source_id", "")
    ]
    if uncited:
        actions.append(
            f"{len(uncited)} hit(s) lack a resolvable citation — verify provenance before use."
        )
    low = [r for r in ranked if (getattr(r, "score", 0.0) or 0.0) < 0.4]
    if low:
        actions.append(
            f"{len(low)} low-confidence passage(s) — corroborate against a second source."
        )
    actions.append("Confirm classification markings and quote precise language over paraphrase.")
    return actions


def triage_summary(results, query: str = "", profile=None, top_n: int = 5) -> dict:
    return grounded_triage(
        results, DOCUMENT_DOMAIN, _HEADERS, _recommended_actions,
        query=query, top_n=top_n,
    )


def di_triage_summary(results, query: str = "", profile=None, top_n: int = 5) -> dict:
    """Triage for the ``document_intelligence`` lens (DIC corpus only)."""
    return grounded_triage(
        results, DOCUMENT_INTELLIGENCE_DOMAIN, _HEADERS, _recommended_actions,
        query=query, top_n=top_n,
    )
