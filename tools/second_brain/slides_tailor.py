# CUI // SP-CTI
"""Audience-tailor slide deck outline based on user's customer/stakeholder registry."""
from __future__ import annotations

import re
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_FRAMING: dict[str, dict] = {
    "boss": {
        "tone": "executive",
        "lead_with": "business impact and risk",
        "avoid": "deep technical detail",
        "open_with": "Bottom line up front (BLUF): state the ask in slide 1.",
    },
    "customer": {
        "tone": "value-focused",
        "lead_with": "outcomes and SLA compliance",
        "avoid": "internal jargon",
        "open_with": "Start with their problem, not your solution.",
    },
    "stakeholder": {
        "tone": "collaborative",
        "lead_with": "alignment and dependencies",
        "avoid": "assuming shared context",
        "open_with": "Frame as a decision request with clear options.",
    },
    "direct": {
        "tone": "technical",
        "lead_with": "implementation details and action items",
        "avoid": "fluff and lengthy preambles",
        "open_with": "Lead with the sprint goal and blockers.",
    },
    "peer": {
        "tone": "collaborative",
        "lead_with": "shared context and coordination",
        "avoid": "hierarchical framing",
        "open_with": "Open with what you need from them early.",
    },
    "vendor": {
        "tone": "evaluative",
        "lead_with": "requirements and evaluation criteria",
        "avoid": "premature commitment",
        "open_with": "Be explicit about scoring criteria.",
    },
}

_DEFAULT_FRAMING = {
    "tone": "professional",
    "lead_with": "key findings and recommendations",
    "avoid": "unnecessary jargon",
    "open_with": "State the purpose of the deck in slide 1.",
}


def get_audience_framing(
    topic: str,
    user_id: str,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Return framing guidance for a deck on *topic* based on user's registry."""
    try:
        from tools.second_brain.profile import get_relationships
        rels = get_relationships(user_id, tenant_id) or []
    except Exception:
        return {
            "personalised": False,
            "framing": _DEFAULT_FRAMING,
            "matched_audience": [],
            "primary_type": None,
            "suggested_openers": [_DEFAULT_FRAMING["open_with"]],
        }

    topic_lower = topic.lower()
    topic_words = {w for w in re.split(r"[^a-z]+", topic_lower) if len(w) > 3}

    scored: list[tuple[float, dict]] = []
    for rel in rels:
        candidate = f"{rel.get('name','')} {rel.get('org','')} {rel.get('notes','')}".lower()
        match_count = sum(1 for w in topic_words if w in candidate)
        if match_count > 0:
            scored.append((match_count, rel))

    if not scored:
        return {
            "personalised": False,
            "framing": _DEFAULT_FRAMING,
            "matched_audience": [],
            "primary_type": None,
            "suggested_openers": [_DEFAULT_FRAMING["open_with"]],
        }

    scored.sort(key=lambda x: x[0], reverse=True)
    primary_rel = scored[0][1]
    primary_type = primary_rel.get("relationship_type", "peer")
    framing = _FRAMING.get(primary_type, _DEFAULT_FRAMING)
    matched_names = [r.get("name", "") for _, r in scored[:3]]

    openers = [framing["open_with"]]
    if len(scored) > 1:
        secondary_type = scored[1][1].get("relationship_type", "")
        if secondary_type and secondary_type != primary_type:
            secondary = _FRAMING.get(secondary_type, {})
            if secondary.get("open_with"):
                openers.append(
                    f"If {scored[1][1].get('name', 'secondary audience')} is also present: {secondary['open_with']}"
                )

    return {
        "personalised": True,
        "framing": framing,
        "matched_audience": matched_names,
        "primary_type": primary_type,
        "suggested_openers": openers,
    }
