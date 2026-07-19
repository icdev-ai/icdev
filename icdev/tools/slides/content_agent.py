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
from pathlib import Path
from typing import Any

from tools.slides.constants import (
    LLM_FN_CONTENT, LLM_FN_REVISION, LLM_FN_MERMAID, LLM_FN_THREE, LLM_FN_EXCALIDRAW,
    LLM_FN_TABLE, TONE_STYLE_HINTS,
    PROVENANCE_LLM, PROVENANCE_FALLBACK, PROVENANCE_STRUCTURAL,
)

_ICDEV_ROOT = Path(__file__).resolve().parents[3]

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


def _extract_type_hint(title: str) -> tuple[str, str | None]:
    """Strip [TYPE:xxx] tag from title and return (clean_title, type_hint|None)."""
    m = re.search(
        r'\[TYPE:(mermaid_diagram|three_animation|excalidraw_sketch|table|card_grid)\]', title
    )
    if m:
        return title[:m.start()].strip(), m.group(1)
    return title, None


def _read_hardprompt(name: str) -> str:
    """Read a slides hardprompt file; return empty string if missing."""
    path = _ICDEV_ROOT / "hardprompts" / "slides" / name
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()


def _generate_mermaid_slide(title: str, raw_content: dict[str, Any], tone: str) -> dict:
    """Generate a Mermaid diagram slide."""
    system_prompt = _read_hardprompt("mermaid_generation.md") or (
        "Generate a Mermaid diagram for the slide topic. Output ONLY raw Mermaid syntax, no fences."
    )
    context = f"Slide title: {title}\nTone: {tone}"
    for src_key, src_data in raw_content.items():
        if isinstance(src_data, dict) and "summary" in src_data:
            context += f"\n[{src_key.upper()}]: {src_data['summary'][:300]}"

    mermaid_code = None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": context}],
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.2,
            agent_id="slides-mermaid",
            classification="CUI",
            effort="medium",
            skip_injection_scan=True,
        )
        response = router.invoke(LLM_FN_MERMAID, request)
        raw = _strip_fences(response.content or "")
        if raw and ("flowchart" in raw or "sequenceDiagram" in raw or "classDiagram" in raw
                    or "stateDiagram" in raw or "-->" in raw):
            mermaid_code = raw
    except Exception:
        pass

    provenance = PROVENANCE_LLM
    if not mermaid_code:
        mermaid_code = f"flowchart LR\n    A[{title[:20]}] --> B[Process] --> C[Result]"
        provenance = PROVENANCE_FALLBACK

    return {
        "title": title,
        "slide_type": "mermaid_diagram",
        "bullets": [],
        "speaker_notes": f"This diagram illustrates {title.lower()}. Walk the audience through each step.",
        "visual_context": f"Mermaid diagram for {title.lower()}",
        "citations": [],
        "mermaid_code": mermaid_code,
        "three_scene_config": None,
        "excalidraw_elements": None,
        "provenance": provenance,
    }


def _generate_three_scene_slide(
    title: str, raw_content: dict[str, Any], tone: str, theme: str = "midnight_executive"
) -> dict:
    """Generate a Three.js 3D scene slide."""
    system_prompt = _read_hardprompt("three_scene_generation.md") or (
        "Generate a Three.js scene_config JSON for the slide topic. Output ONLY valid JSON."
    )
    context = f"Slide title: {title}\nTone: {tone}\nTheme: {theme}"
    for src_key, src_data in raw_content.items():
        if isinstance(src_data, dict) and "summary" in src_data:
            context += f"\n[{src_key.upper()}]: {src_data['summary'][:200]}"

    scene_config = None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": context}],
            system_prompt=system_prompt,
            max_tokens=768,
            temperature=0.3,
            agent_id="slides-three-scene",
            classification="CUI",
            effort="medium",
            skip_injection_scan=True,
        )
        response = router.invoke(LLM_FN_THREE, request)
        raw = _strip_fences(response.content or "")
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "objects" in parsed:
            scene_config = parsed
    except Exception:
        pass

    provenance = PROVENANCE_LLM
    if not scene_config:
        provenance = PROVENANCE_FALLBACK
        scene_config = {
            "background": "#0a1628",
            "camera": {"type": "perspective", "fov": 60, "position": [0, 4, 18]},
            "lights": [
                {"type": "ambient", "color": "#ffffff", "intensity": 0.4},
                {"type": "directional", "color": "#c8a951", "intensity": 0.8, "position": [5, 10, 5]},
            ],
            "objects": [
                {"id": "n1", "type": "sphere", "position": [-5, 0, 0], "color": "#4a90d9",
                 "animation": {"type": "pulse", "speed": 1.0}},
                {"id": "n2", "type": "sphere", "position": [0, 2, 0], "color": "#00b4d8",
                 "animation": {"type": "float", "speed": 0.8}},
                {"id": "n3", "type": "sphere", "position": [5, 0, 0], "color": "#7b2fbe",
                 "animation": {"type": "pulse", "speed": 1.2}},
                {"id": "e1", "type": "line", "from": "n1", "to": "n2", "color": "#c8a95180"},
                {"id": "e2", "type": "line", "from": "n2", "to": "n3", "color": "#c8a95180"},
            ],
            "preset": "neural_network",
        }

    return {
        "title": title,
        "slide_type": "three_animation",
        "bullets": [],
        "speaker_notes": f"This 3D visualization represents {title.lower()}. The animation shows the relationships and flow between components.",
        "visual_context": f"3D animation for {title.lower()}",
        "citations": [],
        "mermaid_code": None,
        "three_scene_config": scene_config,
        "excalidraw_elements": None,
        "provenance": provenance,
    }


def _generate_excalidraw_slide(title: str, raw_content: dict[str, Any], tone: str) -> dict:
    """Generate an Excalidraw-style hand-drawn sketch slide."""
    system_prompt = _read_hardprompt("excalidraw_generation.md") or (
        "Generate Excalidraw element JSON array for the slide. Output ONLY valid JSON array."
    )
    context = f"Slide title: {title}\nTone: {tone}"
    for src_key, src_data in raw_content.items():
        if isinstance(src_data, dict) and "summary" in src_data:
            context += f"\n[{src_key.upper()}]: {src_data['summary'][:200]}"

    elements = None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": context}],
            system_prompt=system_prompt,
            max_tokens=1024,
            temperature=0.3,
            agent_id="slides-excalidraw",
            classification="CUI",
            effort="medium",
            skip_injection_scan=True,
        )
        response = router.invoke(LLM_FN_EXCALIDRAW, request)
        raw = _strip_fences(response.content or "")
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            elements = parsed
    except Exception:
        pass

    provenance = PROVENANCE_LLM
    if not elements:
        provenance = PROVENANCE_FALLBACK
        label = title[:20]
        elements = [
            {"type": "rectangle", "x": 80, "y": 120, "width": 180, "height": 70,
             "strokeColor": "#e8e8e8", "backgroundColor": "transparent",
             "fillStyle": "hachure", "roughness": 1, "strokeWidth": 2},
            {"type": "text", "x": 100, "y": 145, "width": 140, "height": 24,
             "text": label, "fontSize": 18, "strokeColor": "#ffffff"},
            {"type": "arrow", "x": 260, "y": 155, "width": 120, "height": 0,
             "points": [[0, 0], [120, 0]],
             "strokeColor": "#c8a951", "roughness": 1},
            {"type": "rectangle", "x": 380, "y": 120, "width": 180, "height": 70,
             "strokeColor": "#00b4d8", "backgroundColor": "transparent",
             "fillStyle": "hachure", "roughness": 1, "strokeWidth": 2},
            {"type": "text", "x": 400, "y": 145, "width": 140, "height": 24,
             "text": "Outcome", "fontSize": 18, "strokeColor": "#00b4d8"},
        ]

    return {
        "title": title,
        "slide_type": "excalidraw_sketch",
        "bullets": [],
        "speaker_notes": f"This hand-drawn diagram explains {title.lower()} in simple, approachable terms.",
        "visual_context": f"Excalidraw sketch for {title.lower()}",
        "citations": [],
        "mermaid_code": None,
        "three_scene_config": None,
        "excalidraw_elements": elements,
        "provenance": provenance,
    }


def _generate_table_slide(title: str, raw_content: dict[str, Any], tone: str) -> dict:
    """Generate a structured data table slide.

    The 'bullets' field stores table data as a dict:
    {"headers": [...], "rows": [[...], ...], "footer": [...]}
    """
    system_prompt = (
        "You are a presentation data analyst. Generate a structured table for the slide topic.\n"
        "Output ONLY valid JSON with exactly these keys:\n"
        '{"headers": ["Column 1", "Column 2", ...], '
        '"rows": [["row1col1", "row1col2", ...], ...], '
        '"footer": ["Total label", "Total value", ...]}\n'
        "Rules:\n"
        "- 2-4 columns, 3-8 rows\n"
        "- Right-align numeric columns by prefixing header with '$' or '%' hint (model decides)\n"
        "- footer is optional: empty list [] if not needed\n"
        "- Keep cell values concise (under 40 chars)\n"
        "Output ONLY the JSON object, no explanation."
    )
    context = f"Slide title: {title}\nTone: {tone}"
    for src_key, src_data in raw_content.items():
        if isinstance(src_data, dict) and "summary" in src_data:
            context += f"\n[{src_key.upper()}]: {src_data['summary'][:300]}"

    table_data = None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": context}],
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.2,
            agent_id="slides-table",
            classification="CUI",
            effort="medium",
            skip_injection_scan=True,
        )
        response = router.invoke(LLM_FN_TABLE, request)
        raw = _strip_fences(response.content or "")
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "headers" in parsed and "rows" in parsed:
            table_data = parsed
    except Exception:
        pass

    provenance = PROVENANCE_LLM
    if not table_data:
        provenance = PROVENANCE_FALLBACK
        table_data = {
            "headers": ["Item", "Value", "Notes"],
            "rows": [[title[:30], "—", "See speaker notes"]],
            "footer": [],
        }

    return {
        "title": title,
        "slide_type": "table",
        "bullets": table_data,
        "speaker_notes": f"This table summarizes {title.lower()}. Walk the audience through each row.",
        "visual_context": f"Structured data table for {title.lower()}",
        "citations": [],
        "mermaid_code": None,
        "three_scene_config": None,
        "excalidraw_elements": None,
        "provenance": provenance,
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
    slide_type_hint: str | None = None,
    theme: str = "midnight_executive",
    lang_suffix: str = "",
) -> dict:
    """Generate content for a single slide."""
    # Dispatch to rich-type generators before standard path
    if slide_type_hint == "mermaid_diagram":
        return _generate_mermaid_slide(title, raw_content, tone)
    if slide_type_hint == "three_animation":
        return _generate_three_scene_slide(title, raw_content, tone, theme)
    if slide_type_hint == "excalidraw_sketch":
        return _generate_excalidraw_slide(title, raw_content, tone)
    if slide_type_hint == "table":
        return _generate_table_slide(title, raw_content, tone)

    tone_hint = TONE_STYLE_HINTS.get(tone, TONE_STYLE_HINTS["professional"])
    if is_title_slide:
        return {
            "title": title,
            "bullets": [],
            "speaker_notes": f"Welcome to this {tone} presentation on {title}.",
            "visual_context": f"Bold {tone_hint['visual']} title slide with strong typography",
            "slide_type": "title",
            "citations": [],
            # Title slides are intentionally templated, not a degradation.
            "provenance": PROVENANCE_STRUCTURAL,
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
            # Outro slides are intentionally templated, not a degradation.
            "provenance": PROVENANCE_STRUCTURAL,
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
            system_prompt=_build_system_prompt(tone) + lang_suffix,
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
        # Real model output (even if parsed via heuristic) is genuine content.
        parsed["provenance"] = PROVENANCE_LLM
        return parsed
    except Exception:
        pass

    # Static fallback — the LLM was unavailable, so this content is canned.
    fallback = _parse_slide("", title)
    fallback["provenance"] = PROVENANCE_FALLBACK
    return fallback


def _generate_one_with_lang(
    title: str, position: int, raw_content: dict[str, Any],
    is_title_slide: bool, is_outro: bool, tone: str, citation_style: str,
    previous: dict | None, feedback: str | None, slide_type_hint: str | None,
    theme: str, lang_suffix: str,
) -> dict:
    """Wrapper that passes lang_suffix into _generate_one for language-aware output."""
    return _generate_one(
        title=title, position=position, raw_content=raw_content,
        is_title_slide=is_title_slide, is_outro=is_outro, tone=tone,
        citation_style=citation_style, previous=previous, feedback=feedback,
        slide_type_hint=slide_type_hint, theme=theme, lang_suffix=lang_suffix,
    )


def generate_all(
    outline: list[str],
    raw_content: dict[str, Any],
    max_workers: int = 4,
    tone: str = "professional",
    citation_style: str = "inline_links",
    enable_rich_diagrams: bool = False,
    theme: str = "midnight_executive",
    output_language: str = "English",
) -> list[dict]:
    """Generate content for all slides in parallel."""
    if not outline:
        return []

    n = len(outline)
    # Append language suffix to system prompt for non-English decks
    lang_suffix = ""
    if output_language and output_language.lower() != "english":
        lang_suffix = f"\n\nIMPORTANT: Write all slide content (bullets, speaker notes) in {output_language}."

    args_list: list[tuple] = []
    for i, raw_title in enumerate(outline):
        is_title = i == 0
        is_outro = i == n - 1 and n > 1
        # Extract and strip [TYPE:xxx] hints from orchestrator
        if enable_rich_diagrams:
            clean_title, type_hint = _extract_type_hint(raw_title)
        else:
            clean_title, type_hint = raw_title, None
        args_list.append((
            clean_title, i + 1, raw_content, is_title, is_outro, tone, citation_style,
            None, None, type_hint, theme, lang_suffix,
        ))

    results: list[dict | None] = [None] * n
    with ThreadPoolExecutor(max_workers=min(max_workers, n)) as pool:
        futures = {
            pool.submit(_generate_one_with_lang, *args): idx
            for idx, args in enumerate(args_list)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:
                title = outline[idx]
                fallback = _parse_slide("", title)
                fallback["provenance"] = PROVENANCE_FALLBACK
                results[idx] = fallback

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
