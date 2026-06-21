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

from tools.slides.constants import LLM_FN_CONTENT, LLM_FN_REVISION

_SYSTEM_PROMPT = """You are a slide content writer for ICDEV™, a federal AI DevSecOps platform.
Write content for a single PowerPoint slide.

Output ONLY valid JSON with these exact keys:
{
  "title": "<exact slide title>",
  "bullets": ["bullet 1", "bullet 2", "bullet 3"],
  "speaker_notes": "2-4 sentences of presenter guidance.",
  "visual_context": "One sentence describing the ideal visual for this slide.",
  "slide_type": "content"
}

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
- Reference the navy/gold ICDEV color palette when relevant
- Example: "Minimalist isometric diagram of interconnected AI agents on a dark navy background"

Rules for slide_type:
- "content" for standard bullet slides
- "data" when slide is about metrics/numbers
- "two_column" when contrasting two concepts
- "quote" for pull-quote highlights
"""

_REVISION_SUFFIX = """
Previous content: {previous}
User feedback: {feedback}
Revise the slide content incorporating the feedback. Return ONLY valid JSON.
"""


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
        "bullets": bullets or [f"Key capability: {title}"],
        "speaker_notes": " ".join(notes_lines) or f"ICDEV™ delivers {title.lower()} capabilities.",
        "visual_context": f"Professional diagram illustrating {title.lower()} on a dark navy background.",
        "slide_type": "content",
    }


def _generate_one(
    title: str,
    position: int,
    raw_content: dict[str, Any],
    is_title_slide: bool = False,
    is_outro: bool = False,
    previous: dict | None = None,
    feedback: str | None = None,
) -> dict:
    """Generate content for a single slide."""
    if is_title_slide:
        return {
            "title": title,
            "bullets": [],
            "speaker_notes": (
                f"Welcome to this presentation on {title}. "
                "ICDEV™ is a full-stack AI DevSecOps platform purpose-built for federal government."
            ),
            "visual_context": "Bold navy slide with gold ICDEV™ logo, minimalist corporate design",
            "slide_type": "title",
        }
    if is_outro:
        return {
            "title": title,
            "bullets": [
                "Request a live demo at icdev.ai",
                "Schedule a technical deep-dive with our team",
                "Access open-source components on GitHub",
            ],
            "speaker_notes": "Thank you for your time. We welcome the opportunity to demonstrate ICDEV™ capabilities in your environment.",
            "visual_context": "Minimalist outro slide with navy background, gold accent bar, and call-to-action",
            "slide_type": "outro",
        }

    # Build context excerpt for the LLM
    context_parts: list[str] = [f"Slide Title: {title}", f"Position: {position}", ""]
    for src_key, src_data in raw_content.items():
        if isinstance(src_data, dict) and "summary" in src_data:
            context_parts.append(f"[{src_key.upper()}]: {src_data['summary']}")

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
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.35,
            agent_id=f"slides-content-{position}",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )
        response = router.invoke(fn, request)
        raw = response.content or ""
        return _parse_slide(raw, title)
    except Exception:
        pass

    # Static fallback
    return _parse_slide("", title)


def generate_all(
    outline: list[str],
    raw_content: dict[str, Any],
    max_workers: int = 4,
) -> list[dict]:
    """Generate content for all slides in parallel."""
    if not outline:
        return []

    n = len(outline)
    args_list: list[tuple] = []
    for i, title in enumerate(outline):
        is_title = i == 0
        is_outro = i == n - 1 and n > 1
        args_list.append((title, i + 1, raw_content, is_title, is_outro))

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
) -> dict:
    """Revise a single slide based on HITL feedback."""
    return _generate_one(
        title=slide["title"],
        position=slide.get("position", 1),
        raw_content=raw_content,
        previous=slide,
        feedback=feedback,
    )
