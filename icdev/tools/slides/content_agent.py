# CUI // SP-CTI
"""Slide Content Agent — parallel LLM-based bullet/notes generator.

For each slide title, calls the slides_content_generation routing function
to produce bullets and speaker notes. Runs slides in parallel via
ThreadPoolExecutor (capped at slides_config.yaml max_parallel_agents).

Output schema per slide:
  {
    "title":         str,
    "bullets":       list[str],   # 3-5 bullets, ≤15 words each
    "speaker_notes": str,         # 2-4 sentence presenter guidance
    "visual_context": str,        # 1-sentence visual description for graphics gen
    "slide_type":    str,         # content | data | quote | two_column
  }
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from tools.slides.constants import LLM_FN_CONTENT, LLM_FN_REVISION, TONE_STYLE_HINTS

_BASE_SYSTEM_PROMPT = """You are a slide content writer for a presentation deck.
Write content for a single PowerPoint slide.

Output ONLY valid JSON with these exact keys:
{{
  "title": "<exact slide title>",
  "bullets": ["bullet 1", "bullet 2", "bullet 3"],
  "speaker_notes": "2-4 sentences of presenter guidance.",
  "visual_context": "One sentence describing the ideal visual for this slide.",
  "slide_type": "content",
  "citations": [1, 2]
}}

Rules for bullets:
- 3-5 bullets, each ≤15 words
- No leading dashes or bullet characters
- Concrete, specific, action-oriented
- Draw from the source content provided

Rules for speaker_notes:
- 2-4 sentences, natural spoken prose
- Expand on bullets without repeating them verbatim
- Include a relevant stat or concrete example when possible

Rules for visual_context:
- Describe a professional illustration or diagram (no text in the image)
- {visual_hint}

Rules for slide_type:
- "content" for standard bullet slides
- "data" when slide is about metrics/numbers
- "two_column" when contrasting two concepts
- "quote" for pull-quote highlights

Rules for citations:
- If research sources are provided, include a JSON array of 0-3 source indices you relied on.
- If no sources are relevant, return an empty array [] for citations.
"""

_REVISION_SUFFIX = """
Previous content: {previous}
User feedback: {feedback}
Revise the slide content incorporating the feedback. Return ONLY valid JSON.
"""


def _build_system_prompt(tone: str = "professional", visual_hint: str = "") -> str:
    tone_hint = TONE_STYLE_HINTS.get(tone, TONE_STYLE_HINTS["professional"])
    return _BASE_SYSTEM_PROMPT.format(
        tone=tone,
        tone_hint=tone_hint["writing"],
        visual_hint=visual_hint or tone_hint["visual"],
    )


def _parse_slide(raw: str, title: str) -> dict:
    """3-strategy fallback parser for slide content."""
    raw = raw.strip()

    # Strategy 1: direct JSON parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "bullets" in parsed:
            parsed.setdefault("title", title)
            parsed.setdefault("speaker_notes", "")
            parsed.setdefault("visual_context", "")
            parsed.setdefault("slide_type", "content")
            parsed.setdefault("citations", [])
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: regex extract {...} block
    match = re.search(r"\{[\s\S]+\}", raw)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                parsed.setdefault("title", title)
                parsed.setdefault("bullets", [])
                parsed.setdefault("speaker_notes", "")
                parsed.setdefault("visual_context", "")
                parsed.setdefault("slide_type", "content")
                parsed.setdefault("citations", [])
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: line-by-line heuristic
    bullets: list[str] = []
    notes_lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        line = re.sub(r"^[\-\*\•\d\.]+\s*", "", line).strip()
        if 5 <= len(line) <= 120:
            if len(bullets) < 5:
                bullets.append(line)
            else:
                notes_lines.append(line)

    return {
        "title": title,
        "bullets": bullets or [f"Key point: {title}"],
        "speaker_notes": " ".join(notes_lines) or f"Presenter guidance for {title.lower()}.",
        "visual_context": f"Illustration representing {title.lower()}.",
        "slide_type": "content",
        "citations": [],
    }


def _generate_one(
    title: str,
    position: int,
    raw_content: dict[str, Any],
    is_title_slide: bool = False,
    is_outro: bool = False,
    tone: str = "professional",
    citation_style: str = "inline_links",
    previous: dict | None = None,
    feedback: str | None = None,
) -> dict:
    """Generate content for a single slide."""
    tone_hint = TONE_STYLE_HINTS.get(tone, TONE_STYLE_HINTS["professional"])
    if is_title_slide:
        return {
            "title": title,
            "bullets": [],
            "speaker_notes": f"Welcome to this {tone} presentation on {title}.",
            "visual_context": f"Bold {tone_hint['visual']} title slide with strong typography",
            "slide_type": "title",
            "citations": [],
        }
    if is_outro:
        return {
            "title": title,
            "bullets": [
                "Key takeaway recap",
                "Suggested next step for the audience",
                "Contact or follow-up action",
            ],
            "speaker_notes": "Thank the audience and leave them with one clear next action.",
            "visual_context": f"{tone_hint['visual'].capitalize()} closing slide with call-to-action",
            "slide_type": "outro",
            "citations": [],
        }

    # Build context excerpt for the LLM
    context_parts: list[str] = [f"Slide Title: {title}", f"Position: {position}", f"Tone: {tone}", ""]
    for src_key, src_data in raw_content.items():
        if isinstance(src_data, dict) and "summary" in src_data:
            context_parts.append(f"[{src_key.upper()}]: {src_data['summary']}")
            if src_key == "research" and isinstance(src_data.get("sources"), list):
                for idx, src in enumerate(src_data["sources"][:5], start=1):
                    line = f"  [{idx}] {src.get('title', '')}"
                    if src.get("url"):
                        line += f" — {src['url']}"
                    if src.get("snippet"):
                        line += f": {src['snippet']}"
                    context_parts.append(line)

    context_str = "\n".join(context_parts)
    if previous and feedback:
        user_msg = context_str + "\n" + _REVISION_SUFFIX.format(
            previous=json.dumps(previous), feedback=feedback
        )
        fn = LLM_FN_REVISION
    else:
        user_msg = context_str
        fn = LLM_FN_CONTENT

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_build_system_prompt(tone),
            max_tokens=768,
            temperature=0.35,
            agent_id=f"slides-content-{position}",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )
        response = router.invoke(fn, request)
        raw = response.content or ""
        parsed = _parse_slide(raw, title)
        # Resolve citation indices to source dicts if research sources exist
        research_sources = (raw_content.get("research") or {}).get("sources", [])
        citation_indices = parsed.get("citations") or []
        if research_sources and citation_indices:
            parsed["citations"] = [
                research_sources[int(i) - 1]
                for i in citation_indices
                if isinstance(i, int) and 1 <= i <= len(research_sources)
            ]
        return parsed
    except Exception:
        pass

    # Static fallback
    return _parse_slide("", title)


def generate_all(
    outline: list[str],
    raw_content: dict[str, Any],
    max_workers: int = 4,
    tone: str = "professional",
    citation_style: str = "inline_links",
) -> list[dict]:
    """Generate content for all slides in parallel."""
    if not outline:
        return []

    n = len(outline)
    args_list: list[tuple] = []
    for i, title in enumerate(outline):
        is_title = i == 0
        is_outro = i == n - 1 and n > 1
        args_list.append((title, i + 1, raw_content, is_title, is_outro, tone, citation_style))

    results: list[dict | None] = [None] * n
    with ThreadPoolExecutor(max_workers=min(max_workers, n)) as pool:
        futures = {
            pool.submit(_generate_one, *args): idx
            for idx, args in enumerate(args_list)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:
                title = outline[idx]
                results[idx] = _parse_slide("", title)

    return [r for r in results if r is not None]


def revise_slide(
    slide: dict,
    feedback: str,
    raw_content: dict[str, Any],
    tone: str | None = None,
) -> dict:
    """Revise a single slide based on HITL feedback."""
    return _generate_one(
        title=slide["title"],
        position=slide.get("position", 1),
        raw_content=raw_content,
        previous=slide,
        feedback=feedback,
        tone=tone or slide.get("tone", "professional"),
    )
