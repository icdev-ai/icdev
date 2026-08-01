# CUI // SP-CTI
"""Proposal / capture domain lens — triage formatter.

Capture-manager framing over retrieved solicitation / requirement / past-
performance hits: requirement-coverage first, then win-theme evidence, then
gaps. Deterministic and grounded by construction (every line derives from a
real retrieved row).
"""
from __future__ import annotations

from typing import List

from ._triage import grounded_triage

PROPOSAL_DOMAIN = "proposal"

_HEADERS = {
    "title": "Capture triage",
    "empty": "No proposal-scoped findings.",
    "evidence": "Requirement coverage / evidence (confidence-ranked):",
    "coverage": "Coverage",
    "actions": "Recommended capture moves:",
}


def _recommended_actions(ranked: list) -> List[str]:
    """Capture actions derived from the retrieved evidence (never invented)."""
    actions: List[str] = []
    if not ranked:
        return ["No evidence retrieved — broaden the search or ingest the solicitation."]
    low = [r for r in ranked if (getattr(r, "score", 0.0) or 0.0) < 0.4]
    if low:
        actions.append(
            f"{len(low)} weakly-supported requirement(s) — gather stronger past-performance "
            "evidence or flag as a compliance gap."
        )
    actions.append("Map each retrieved requirement to a win theme / discriminator.")
    actions.append("Confirm every claim traces to cited past performance before drafting.")
    return actions


def triage_summary(results, query: str = "", profile=None, top_n: int = 5) -> dict:
    return grounded_triage(
        results, PROPOSAL_DOMAIN, _HEADERS, _recommended_actions,
        query=query, top_n=top_n,
    )
