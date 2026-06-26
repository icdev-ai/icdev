# CUI // SP-CTI
"""Slide Graphics Generator — two-stage contextual image pipeline.

Stage 1: LLM generates a rich, descriptive image prompt from slide content.
Stage 2: Image generation API (Ollama cloud / DALL-E / Gemini / Pillow fallback).

Provider selection via SLIDES_IMAGE_PROVIDER env var or args/slides_config.yaml.
Safe default: matplotlib (always available, air-gap safe).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path

from tools.slides.constants import LLM_FN_VIZ_PROMPT, THEME_PALETTES, DEFAULT_THEME, TONE_STYLE_HINTS

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


class GraphicsGenerator:
    """Two-stage graphics generation: visual prompt → image API → file."""

    def __init__(self, output_dir: Path | None = None):
        self._provider = os.environ.get("SLIDES_IMAGE_PROVIDER", "matplotlib").lower()
        self._model = os.environ.get("SLIDES_IMAGE_MODEL", "sdxl:latest")
        self._timeout = int(os.environ.get("SLIDES_IMAGE_TIMEOUT", "30"))
        self._enabled = os.environ.get("SLIDES_IMAGE_ENABLED", "true").lower() in ("true", "1", "yes")

        root = Path(__file__).resolve().parents[3]
        self._output_dir = output_dir or (root / "tools" / "presentations" / "slides" / "images")
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, title: str, bullets: list[str], visual_context: str = "", theme: str = "", tone: str = "") -> str | None:
        """Generate an image for a slide. Returns absolute file path or None on skip."""
        if not self._enabled:
            return None

        # Stage 1: generate a rich image prompt
        prompt = self._build_visual_prompt(title, bullets, visual_context, theme, tone)
        if not prompt:
            return None

        # Stage 2: call the image generation backend
        try:
            img_bytes = self._call_image_api(prompt, title)
        except Exception:
            img_bytes = None

        if img_bytes:
            return self._save_image(img_bytes, title)

        # Fallback: programmatic matplotlib graphic
        return self._matplotlib_fallback(title, bullets, theme, tone)

    # ── Stage 1: Visual Prompt Generation ────────────────────────────────────

    def _style_hint(self, theme: str = "", tone: str = "") -> str:
        """Compose a style hint from theme palette and tone."""
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

    def _build_visual_prompt(self, title: str, bullets: list[str], visual_context: str, theme: str = "", tone: str = "") -> str:
        """Use LLM to generate a descriptive image prompt."""
        style_hint = self._style_hint(theme, tone)
        if visual_context:
            base = f"{visual_context}, {style_hint}"
        else:
            base = f"{title}, {style_hint}"

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
            response = router.invoke(LLM_FN_VIZ_PROMPT, request)
            prompt = (response.content or "").strip().strip('"\'')
            if len(prompt) > 20:
                return prompt
        except Exception:
            pass
        return base

    # ── Stage 2: Image Generation Backends ───────────────────────────────────

    def _call_image_api(self, prompt: str, title: str) -> bytes | None:
        """Dispatch to the configured image generation backend."""
        if self._provider == "ollama_cloud":
            return self._ollama_cloud_gen(prompt)
        if self._provider == "dalle":
            return self._dalle_gen(prompt)
        if self._provider == "gemini":
            return self._gemini_gen(prompt)
        return None  # matplotlib fallback handled by caller

    def _ollama_cloud_gen(self, prompt: str) -> bytes | None:
        """POST to Ollama cloud image generation endpoint."""
        import urllib.request
        base_url = os.environ.get("OLLAMA_CLOUD_BASE_URL", "https://ollama.com").rstrip("/")
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/v1/images/generations",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('OLLAMA_API_KEY', '')}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read())
        # OpenAI-compatible response: data[0].b64_json or data[0].url
        if "data" in data and data["data"]:
            item = data["data"][0]
            if "b64_json" in item:
                return base64.b64decode(item["b64_json"])
            if "url" in item:
                with urllib.request.urlopen(item["url"], timeout=self._timeout) as r:
                    return r.read()
        return None

    def _dalle_gen(self, prompt: str) -> bytes | None:
        """Generate via OpenAI DALL-E 3."""
        import urllib.request
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        payload = json.dumps({
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x576",
            "response_format": "b64_json",
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read())
        b64 = data["data"][0]["b64_json"]
        return base64.b64decode(b64)

    def _gemini_gen(self, prompt: str) -> bytes | None:
        """Generate via Gemini Imagen 3."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=os.environ.get("GOOGLE_API_KEY", ""))
            model = genai.ImageGenerationModel("imagen-3.0-generate-001")
            result = model.generate_images(prompt=prompt, number_of_images=1, aspect_ratio="16:9")
            if result.images:
                return result.images[0]._pil_image.tobytes()  # type: ignore
        except Exception:
            pass
        return None

    # ── Pillow/matplotlib Fallback ────────────────────────────────────────────

    def _matplotlib_fallback(self, title: str, bullets: list[str], theme: str = "", tone: str = "") -> str | None:
        """Generate a programmatic themed graphic using matplotlib."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            import numpy as np

            palette = THEME_PALETTES.get(theme) or THEME_PALETTES[DEFAULT_THEME]
            bg_hex = "#{:02x}{:02x}{:02x}".format(*palette["bg"])
            accent_hex = "#{:02x}{:02x}{:02x}".format(*palette["accent"])
            text_hex = "#{:02x}{:02x}{:02x}".format(*palette["text"])

            fig, ax = plt.subplots(figsize=(10.24, 5.76), facecolor=bg_hex)
            ax.set_facecolor(bg_hex)
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 6)
            ax.axis("off")

            # Accent bar
            ax.add_patch(mpatches.FancyBboxPatch(
                (0, 5.7), 10, 0.3, boxstyle="square,pad=0",
                facecolor=accent_hex, linewidth=0
            ))

            # Abstract background shapes
            rng = np.random.default_rng(abs(hash(title)) % (2**31))
            for _ in range(6):
                cx, cy = rng.uniform(1, 9), rng.uniform(0.5, 5)
                r = rng.uniform(0.3, 1.2)
                alpha = rng.uniform(0.05, 0.2)
                circle = plt.Circle((cx, cy), r, color=accent_hex, alpha=alpha)
                ax.add_patch(circle)

            # Connection lines
            pts = [(rng.uniform(2, 8), rng.uniform(1, 4)) for _ in range(5)]
            for i in range(len(pts) - 1):
                ax.plot(
                    [pts[i][0], pts[i+1][0]], [pts[i][1], pts[i+1][1]],
                    color=accent_hex, alpha=0.3, linewidth=1.5
                )
            # Node dots
            for px, py in pts:
                ax.plot(px, py, "o", color=accent_hex, markersize=8, alpha=0.7)

            # Title text
            short_title = title[:50] + ("…" if len(title) > 50 else "")
            ax.text(
                5, 2.5, short_title, ha="center", va="center",
                color=text_hex, fontsize=13, fontweight="bold",
                wrap=True, family="monospace",
            )

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=96, bbox_inches="tight", facecolor=bg_hex)
            plt.close(fig)
            buf.seek(0)
            img_bytes = buf.read()
            return self._save_image(img_bytes, title)
        except Exception:
            return None

    def _save_image(self, img_bytes: bytes, title: str) -> str:
        """Save image bytes to a file. Returns absolute path."""
        slug = hashlib.sha256(title.encode()).hexdigest()[:12]
        path = self._output_dir / f"slide_{slug}.png"
        path.write_bytes(img_bytes)
        return str(path)
