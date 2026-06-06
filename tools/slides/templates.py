# CUI // SP-CTI
"""Deck templates + slide-layout presets for the Presentation Studio.

Templates give non-technical users a ready-made starting deck (Canva/PowerPoint
style): pick a template → get a structured deck you fill in. Slides are plain
dicts (slide_type/title/bullets); the editor auto-lays them out into editable
elements, then a single theme unifies the look (Figma Slides' "templates →
unifying theme" pattern).
"""
from __future__ import annotations

from typing import Any

DECK_TEMPLATES: dict[str, dict[str, Any]] = {
    "blank": {
        "name": "Blank",
        "description": "A single empty slide — start from scratch.",
        "slides": [{"slide_type": "title", "title": "Untitled Presentation", "bullets": []}],
    },
    "pitch": {
        "name": "Pitch Deck",
        "description": "Problem → solution → proof → ask. Classic 6-slide pitch.",
        "slides": [
            {"slide_type": "title", "title": "Company / Product Name", "bullets": [],
             "speaker_notes": "One-line tagline goes here."},
            {"slide_type": "content", "title": "The Problem",
             "bullets": ["Who has this problem", "Why it matters today", "What it costs them"]},
            {"slide_type": "content", "title": "Our Solution",
             "bullets": ["What we built", "How it works", "Why it's different"]},
            {"slide_type": "content", "title": "How It Works",
             "bullets": ["Step 1", "Step 2", "Step 3"]},
            {"slide_type": "content", "title": "Traction & Proof",
             "bullets": ["Key metric", "Notable customer or result", "Momentum"]},
            {"slide_type": "outro", "title": "The Ask",
             "bullets": ["What we're seeking", "How to reach us"]},
        ],
    },
    "status": {
        "name": "Status Update",
        "description": "Weekly/sprint status: highlights, progress, risks, next.",
        "slides": [
            {"slide_type": "title", "title": "Status Update", "bullets": []},
            {"slide_type": "content", "title": "Highlights", "bullets": ["Win 1", "Win 2", "Win 3"]},
            {"slide_type": "data", "title": "Progress", "bullets": ["Add a chart of progress here"]},
            {"slide_type": "content", "title": "Risks & Blockers", "bullets": ["Risk 1", "Mitigation"]},
            {"slide_type": "outro", "title": "Next Steps", "bullets": ["Priority 1", "Priority 2"]},
        ],
    },
    "comparison": {
        "name": "Comparison / Options",
        "description": "Frame a decision: options, criteria, recommendation.",
        "slides": [
            {"slide_type": "title", "title": "Options & Recommendation", "bullets": []},
            {"slide_type": "content", "title": "The Decision", "bullets": ["What we're deciding", "Why now"]},
            {"slide_type": "data", "title": "Options Compared", "bullets": ["Add a comparison table here"]},
            {"slide_type": "content", "title": "Recommendation", "bullets": ["Recommended option", "Rationale"]},
            {"slide_type": "outro", "title": "Next Steps", "bullets": ["Decision owner", "Timeline"]},
        ],
    },
    "briefing": {
        "name": "Executive Briefing",
        "description": "Crisp leadership briefing: situation, analysis, ask.",
        "slides": [
            {"slide_type": "title", "title": "Executive Briefing", "bullets": []},
            {"slide_type": "content", "title": "Situation", "bullets": ["Context", "What changed"]},
            {"slide_type": "content", "title": "Analysis", "bullets": ["Finding 1", "Finding 2"]},
            {"slide_type": "data", "title": "Key Metrics", "bullets": ["Add KPIs here"]},
            {"slide_type": "outro", "title": "Decision Requested", "bullets": ["The ask", "Impact"]},
        ],
    },
}


def list_deck_templates() -> list[dict]:
    return [{"key": k, "name": v["name"], "description": v["description"], "slides": len(v["slides"])}
            for k, v in DECK_TEMPLATES.items()]


def build_from_template(key: str) -> list[dict]:
    """Return a fresh copy of a template's slide dicts (deep-ish copy)."""
    tpl = DECK_TEMPLATES.get(key)
    if not tpl:
        return []
    out = []
    for s in tpl["slides"]:
        out.append({
            "slide_type": s.get("slide_type", "content"),
            "title": s.get("title", ""),
            "bullets": list(s.get("bullets", [])),
            "speaker_notes": s.get("speaker_notes", ""),
        })
    return out
