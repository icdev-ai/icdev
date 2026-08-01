# CUI // SP-CTI
"""Slide Deck Orchestrator — LLM-based outline planner.

Takes raw gathered content and produces a list of slide titles using
the slides_outline_planning LLM routing function.

Implements GenSlide's 3-strategy JSON fallback parser:
  1. Direct JSON parse
  2. Regex extract first [...] block
  3. Line-by-line heuristic (numbered/bulleted)
"""
from __future__ import annotations

import json
import re
from typing import Any

from tools.slides.constants import (
    LLM_FN_OUTLINE, MIN_SLIDES, DEFAULT_MAX_SLIDES, TONE_STYLE_HINTS, AUDIENCE_MODE_HINTS,
)

_ICDEV_SYSTEM_PROMPT = """You are a presentation architect for a US federal AI DevSecOps platform called ICDEV™.
Your task: given raw content about ICDEV's capabilities, design a compelling slide deck outline.

Rules:
- Return BETWEEN {min_slides} AND {max_slides} slide titles
- Each title: 3-8 words, clear and action-oriented
- Logical narrative arc: problem → solution → capabilities → proof → call-to-action
- Skip generic slides like "Introduction" or "Q&A"
- First slide: title/cover (e.g. "ICDEV™: A System That Builds Systems")
- Last slide: call-to-action or next steps
- Return ONLY a JSON array of strings. Example: ["Title One", "Title Two", "Title Three"]
"""

_GENERAL_SYSTEM_PROMPT = """You are a presentation architect for a general-purpose, occasion-aware slide deck.

Topic/Title: {deck_title}
Occasion: {occasion}
Audience: {target_audience}
Tone: {tone}
Tone guidance: {tone_hint}

Rules:
- Return BETWEEN {min_slides} AND {max_slides} slide titles.
- Each title: 3-8 words, clear and action-oriented.
- Build a narrative arc that fits the occasion: hook → context → key points → takeaway → closing.
- Skip generic slides like "Introduction" or "Q&A".
- First slide: title/cover.
- Last slide: closing or call-to-action appropriate to the occasion and audience.
- Return ONLY a JSON array of strings. Example: ["Title One", "Title Two", "Title Three"]
"""

_REVISION_SUFFIX = """
Previous outline: {previous_outline}
User feedback: {feedback}
Revise the outline incorporating the feedback. Return ONLY a JSON array of strings.
"""

_AUDIENCE_HINT = """
Audience mode: {audience_mode}
Narrative arc to follow: {narrative}
Emphasis: {emphasis}
Structure your slide titles to follow this narrative arc exactly.
"""

_RICH_DIAGRAM_HINT = """
For complex concept slides, append a type tag to the title:
  [TYPE:mermaid_diagram] — for flows, sequences, architectures, pipelines
  [TYPE:three_animation] — for neural networks, 3D systems, data pipelines, AI concepts
  [TYPE:excalidraw_sketch] — for hand-drawn concept maps and "how it works" diagrams
Max 3 rich-type slides per deck. Example: "Data Ingestion Pipeline [TYPE:mermaid_diagram]"
"""


def _parse_titles(raw: str) -> list[str]:
    """3-strategy fallback parser for slide title extraction."""
    raw = raw.strip()

    # Strategy 1: direct JSON parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(t).strip() for t in parsed if str(t).strip()]
        if isinstance(parsed, dict):
            for key in ("outline", "titles", "slides", "items"):
                if key in parsed and isinstance(parsed[key], list):
                    return [str(t).strip() for t in parsed[key] if str(t).strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: regex extract first [...] block
    match = re.search(r"\[([^\[\]]+)\]", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads("[" + match.group(1) + "]")
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except (json.JSONDecodeError, ValueError):
            pass
    # Also try quoted strings
    quoted = re.findall(r'"([^"]{5,80})"', raw)
    if quoted:
        return [t.strip() for t in quoted]

    # Strategy 3: line-by-line heuristic
    titles: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        # Strip leading bullets, numbers, dashes
        line = re.sub(r"^[\d\.\-\*\•]+\s*", "", line).strip()
        # Strip surrounding quotes
        line = line.strip('"\'')
        if 10 <= len(line) <= 120 and not line.startswith("{") and not line.startswith("["):
            titles.append(line)
    return titles


def plan_outline(
    raw_content: dict[str, Any],
    deck_title: str,
    deck_type: str = "executive_overview",
    tone: str = "professional",
    occasion: str = "",
    target_audience: str = "",
    min_slides: int = MIN_SLIDES,
    max_slides: int = DEFAULT_MAX_SLIDES,
    previous_outline: list[str] | None = None,
    feedback: str | None = None,
    enable_rich_diagrams: bool = False,
    audience_mode: str | None = None,
    output_language: str = "English",
    return_provenance: bool = False,
) -> list[str] | tuple[list[str], bool]:
    """Call LLM to produce a slide title outline.

    Falls back to a static outline if LLM is unavailable.

    When ``return_provenance`` is True, returns ``(titles, used_fallback)``
    where ``used_fallback`` is True if the canned static outline was returned
    (LLM unavailable/failed). Default False preserves the plain-list return so
    existing callers are unaffected.
    """
    # Build content summary for LLM
    is_general = deck_type == "general_presentation"
    content_parts: list[str] = [f"Deck Title: {deck_title}", f"Deck Type: {deck_type}", ""]
    for source_key, source_data in raw_content.items():
        if isinstance(source_data, dict) and "summary" in source_data:
            content_parts.append(f"[{source_key.upper()}]")
            content_parts.append(source_data["summary"])
            content_parts.append("")

    content_str = "\n".join(content_parts)

    if is_general:
        tone_hint = TONE_STYLE_HINTS.get(tone, TONE_STYLE_HINTS["professional"])["writing"]
        system = _GENERAL_SYSTEM_PROMPT.format(
            deck_title=deck_title,
            occasion=occasion or "general presentation",
            target_audience=target_audience or "general audience",
            tone=tone,
            tone_hint=tone_hint,
            min_slides=min_slides,
            max_slides=max_slides,
        )
    else:
        system = _ICDEV_SYSTEM_PROMPT.format(min_slides=min_slides, max_slides=max_slides)

    # Inject audience mode narrative hint
    if audience_mode and audience_mode in AUDIENCE_MODE_HINTS:
        hints = AUDIENCE_MODE_HINTS[audience_mode]
        system += _AUDIENCE_HINT.format(
            audience_mode=audience_mode,
            narrative=hints["narrative"],
            emphasis=hints["emphasis"],
        )

    # Inject rich diagram type hint
    if enable_rich_diagrams:
        system += _RICH_DIAGRAM_HINT

    # Inject language instruction when non-English
    if output_language and output_language.lower() != "english":
        system += f"\n\nIMPORTANT: Write all slide titles in {output_language}."

    if previous_outline and feedback:
        user_msg = content_str + "\n" + _REVISION_SUFFIX.format(
            previous_outline=json.dumps(previous_outline),
            feedback=feedback,
        )
    else:
        user_msg = content_str

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            max_tokens=1024,
            temperature=0.2,
            agent_id="slides-orchestrator",
            classification="CUI",
            effort="medium",
            skip_injection_scan=True,
        )
        response = router.invoke(LLM_FN_OUTLINE, request)
        raw = response.content or ""
        titles = _parse_titles(raw)
        if len(titles) >= 2:
            titles = titles[:max_slides]
            return (titles, False) if return_provenance else titles
    except Exception:
        pass

    # Static fallback outline — canned structure, flagged as such.
    fallback = _static_outline(deck_type, deck_title, min_slides, occasion, target_audience, tone)
    return (fallback, True) if return_provenance else fallback


def _static_outline(
    deck_type: str, deck_title: str, min_slides: int,
    occasion: str = "", target_audience: str = "", tone: str = "professional"
) -> list[str]:
    """Return a static outline when LLM is unavailable."""
    base: list[str] = [deck_title]
    if deck_type == "general_presentation":
        base += [
            "Why This Topic Matters Now",
            "The Big Picture",
            "Key Insights for " + (target_audience or "Your Audience"),
            "Practical Takeaways",
            "What Comes Next",
        ]
        if tone in ("fun", "creative", "adventurous"):
            base[1] = "Setting the Scene"
            base[4] = "Your Next Adventure"
    elif deck_type == "weekly_status":
        base += [
            "This Week's Highlights",
            "Project Pipeline Status",
            "Active Reflexes and Autonomous Operations",
            "Compliance and Security Posture",
            "Key Achievements and Milestones",
            "Upcoming Priorities",
        ]
    elif deck_type == "govcon_proposal":
        base += [
            "The Federal IT Challenge",
            "ICDEV™ Platform Overview",
            "Core Capabilities and Differentiators",
            "Compliance and ATO Acceleration",
            "Past Performance and Proven Results",
            "Proposed Solution and Timeline",
            "Why ICDEV™",
        ]
    elif deck_type == "compliance_briefing":
        base += [
            "Compliance Posture Summary",
            "FedRAMP / CMMC Status",
            "Open POA&Ms and Remediation Plan",
            "cATO Evidence and Control Coverage",
            "Upcoming Compliance Milestones",
        ]
    else:
        base += [
            "The Problem We Solve",
            "ICDEV™ Platform Architecture",
            "Design Canvases: AI-Assisted Engineering",
            "Autonomous Operations: Genesis Daemon",
            "GovCon Intelligence Pipeline",
            "Compliance Automation at Scale",
            "Get Started with ICDEV™",
        ]
    return base[:max(min_slides, len(base))]
