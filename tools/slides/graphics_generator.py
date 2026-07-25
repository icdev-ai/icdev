# CUI // SP-CTI
"""Slide Graphics Generator — two-stage contextual image pipeline.

Stage 1: LLM generates a rich, descriptive image prompt from slide content.
Stage 2: Unified ICDEV-native asset generator (SDXL Turbo / matplotlib / SVG).

Provider selection via SLIDES_IMAGE_PROVIDER env var is normalized and passed
through to the asset generator.  Cloud providers are intentionally not used in
native-only mode; they fall back to the deterministic ICDEV-native pipeline.

Safe default: matplotlib/SVG (always available, air-gap safe).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from tools.viz.asset_generator import AssetGenerator, AssetRequest

_DEFAULT_STYLE_HINT = (
    "professional corporate illustration, dark navy blue and gold color palette, "
    "minimalist isometric or flat design style, no text labels, no words, "
    "high quality, 16:9 aspect ratio"
)

_VIZ_SYSTEM_TEMPLATE = """You are a visual director for a presentation slide.
Given a slide title and bullet points, write a single detailed image generation prompt
for a professional illustration that visually represents the slide's content.

Rules:
- Describe a concrete visual scene or diagram (NOT a photo of people)
- Use this style/palette guidance: {style_hint}
- Reference the specific subject matter when relevant
- NO text, labels, or words in the image
- Return ONLY the image prompt as plain text, no JSON, no quotes
"""


def _style_hint(theme: str = "", tone: str = "") -> str:
    """Compose a style hint from theme palette and tone."""
    from tools.slides.constants import TONE_STYLE_HINTS, THEME_PALETTES

    parts = []
    if tone:
        tone_hint = TONE_STYLE_HINTS.get(tone, TONE_STYLE_HINTS["professional"])
        parts.append(tone_hint["visual"])
    palette = THEME_PALETTES.get(theme)
    if palette:
        bg_hex = "#{:02x}{:02x}{:02x}".format(*palette["bg"])
        accent_hex = "#{:02x}{:02x}{:02x}".format(*palette["accent"])
        parts.append(f"color palette: background {bg_hex}, accent {accent_hex}")
    if not parts:
        return _DEFAULT_STYLE_HINT
    return "; ".join(parts) + "; no text labels, no words, high quality, 16:9 aspect ratio"


def _build_visual_prompt(title: str, bullets: list[str], visual_context: str, theme: str = "", tone: str = "") -> Optional[str]:
    """Use LLM to generate a descriptive image prompt."""
    style_hint = _style_hint(theme, tone)
    if visual_context:
        base = f"{visual_context}, {style_hint}"
    else:
        base = f"{title}, {style_hint}"

    # Air-gap / native-only mode: never make an external LLM call for prompt
    # expansion. In this mode cloud providers are filtered out and only the
    # native SVG/matplotlib/SDXL providers run — the SVG and matplotlib
    # renderers ignore ``req.prompt`` entirely, so the deterministic ``base``
    # prompt is sufficient and keeps the pipeline fully offline.
    from tools.viz.asset_generator import is_air_gap_media_mode

    if is_air_gap_media_mode():
        return base

    user_msg = (
        f"Slide title: {title}\n"
        f"Bullets: {'; '.join(bullets[:3])}\n"
        "Write an image generation prompt for this slide."
    )
    system_prompt = _VIZ_SYSTEM_TEMPLATE.format(style_hint=style_hint)
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system_prompt,
            max_tokens=200,
            temperature=0.4,
            agent_id="slides-visual-prompt",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )
        from tools.slides.constants import LLM_FN_VIZ_PROMPT
        response = router.invoke(LLM_FN_VIZ_PROMPT, request)
        prompt = (response.content or "").strip().strip('"\'')
        if len(prompt) > 20:
            return prompt
    except Exception:
        pass
    return base


class GraphicsGenerator:
    """Two-stage graphics generation: visual prompt → unified asset generator."""

    def __init__(self, output_dir: Path | None = None):
        raw_provider = os.environ.get("SLIDES_IMAGE_PROVIDER", "matplotlib").lower().replace("-", "_")
        # Normalize cloud/non-native names to native equivalents where possible.
        provider_map = {
            "ollama_cloud": "pulse_sdxl",
            "ollama": "pulse_sdxl",
            "sdxl": "pulse_sdxl",
            "matplotlib": "slides_matplotlib",
            "svg": "slides_svg",
        }
        self._provider = provider_map.get(raw_provider, raw_provider)
        self._enabled = os.environ.get("SLIDES_IMAGE_ENABLED", "true").lower() in ("true", "1", "yes")
        self._generator = AssetGenerator(output_dir=output_dir)

    def generate(self, title: str, bullets: list[str], visual_context: str = "", theme: str = "", tone: str = "") -> str | None:
        """Generate an image for a slide. Returns absolute file path or None on skip."""
        if not self._enabled:
            return None

        prompt = _build_visual_prompt(title, bullets, visual_context, theme, tone)
        if not prompt:
            return None

        req = AssetRequest(
            context="slides",
            title=title,
            bullets=bullets,
            visual_context=visual_context,
            prompt=prompt,
            theme=theme,
            tone=tone,
            preferred_providers=[self._provider] if self._provider else [],
        )
        result = self._generator.generate(req)
        return result.get("path") if result.get("success") else None
