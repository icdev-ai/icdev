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

from tools.slides.constants import LLM_FN_OUTLINE, MIN_SLIDES, DEFAULT_MAX_SLIDES

_SYSTEM_PROMPT = """You are a presentation architect for a US federal AI DevSecOps platform called ICDEV™.
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

_REVISION_SUFFIX = """
Previous outline: {previous_outline}
User feedback: {feedback}
Revise the outline incorporating the feedback. Return ONLY a JSON array of strings.
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
    min_slides: int = MIN_SLIDES,
    max_slides: int = DEFAULT_MAX_SLIDES,
    previous_outline: list[str] | None = None,
    feedback: str | None = None,
) -> list[str]:
    """Call LLM to produce a slide title outline.

    Falls back to a static outline if LLM is unavailable.
    """
    # Build content summary for LLM
    content_parts: list[str] = [f"Deck Title: {deck_title}", f"Deck Type: {deck_type}", ""]
    for source_key, source_data in raw_content.items():
        if isinstance(source_data, dict) and "summary" in source_data:
            content_parts.append(f"[{source_key.upper()}]")
            content_parts.append(source_data["summary"])
            content_parts.append("")

    content_str = "\n".join(content_parts)

    system = _SYSTEM_PROMPT.format(min_slides=min_slides, max_slides=max_slides)
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
            return titles[:max_slides]
    except Exception:
        pass

    # Static fallback outline
    return _static_outline(deck_type, deck_title, min_slides)


def _static_outline(deck_type: str, deck_title: str, min_slides: int) -> list[str]:
    """Return a static outline when LLM is unavailable."""
    base: list[str] = [deck_title]
    if deck_type == "weekly_status":
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
