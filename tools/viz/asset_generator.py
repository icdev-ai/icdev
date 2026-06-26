# CUI // SP-CTI
"""ICDEV-native media asset generator — unified dispatcher for images and graphics.

Unifies existing slide graphics and Pulse hero image generation into a single
air-gap-aware, deterministic, cacheable dispatcher.

Providers (ICDEV-native only):
  pulse_sdxl      — local SDXL Turbo GPU generation
  slides_matplotlib — programmatic themed matplotlib graphics
  slides_svg      — deterministic SVG fallback

Provider selection respects:
  1. Explicit request.preferred_providers
  2. args/media_providers.yaml
  3. Air-gap mode (removes cloud/external providers)
  4. Runtime availability (GPU, deps)
  5. Fallback chain ending in SVG
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_IMAGE_DIR = Path(__file__).resolve().parents[3] / "data" / "generated_assets"
DEFAULT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

SDXL_TURBO_MODEL_ID = "stabilityai/sdxl-turbo"
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 576
DEFAULT_STEPS = 4
DEFAULT_GUIDANCE = 0.0

NATIVE_PROVIDERS: frozenset[str] = frozenset({
    "pulse_sdxl",
    "slides_matplotlib",
    "slides_svg",
    "viz_kernel",
})

# Topic keyword → concrete visual scene. Reused from Pulse image_generator.
_TOPIC_VISUAL_MAP: Dict[str, str] = {
    "compliance": "government building with digital shield overlay, regulatory documents, checkmarks on screens",
    "ato": "security authorization dashboard with green status indicators, federal seal, compliance checklist on screen",
    "fedramp": "cloud infrastructure with security shield, federal compliance certification badges on monitors",
    "cmmc": "military defense network with layered security barriers, certification badge",
    "zero trust": "network diagram with locked nodes and encrypted connections, identity verification at every point",
    "devsecops": "software development pipeline with security gates, code on screens with shield icons, CI/CD flow",
    "agentic": "multiple AI robot agents collaborating around a holographic blueprint, building architecture together",
    "security": "cybersecurity operations center with threat dashboards, lock icons, network monitoring screens",
    "sbom": "software supply chain diagram with component boxes, dependency tree, vulnerability scan results",
    "cato": "continuous monitoring dashboard with real-time compliance gauges, evidence timeline",
    "modernization": "legacy mainframe transforming into modern cloud microservices",
    "kubernetes": "container orchestration dashboard, pod status indicators, service mesh",
    "terraform": "infrastructure as code, cloud architecture diagram generated from code",
    "ai": "artificial intelligence neural network visualization, machine learning workflow",
    "network": "enterprise network architecture diagram with routers, firewalls, VPN tunnels",
    "market intelligence": "stock market trading terminal with candlestick charts, financial data screens",
    "trading": "stock market trading terminal with candlestick charts, multiple monitors",
    "finance": "financial analytics dashboard with charts, portfolio metrics",
    "portfolio": "investment portfolio dashboard with asset allocation pie charts",
    "intelligence": "intelligence operations center with multiple data feeds, correlation maps",
    "research": "research lab with data visualization dashboards, trend analysis",
    "proposal": "government proposal review workspace with compliance checklists",
    "govcon": "government contracting office with procurement data, proposal volumes",
    "cloud": "cloud computing infrastructure with virtual servers, load balancers, multi-region architecture",
    "program management": "digital program management dashboard with Gantt charts, earned value metrics",
}

_pipeline_cache: Any = None


@dataclass
class AssetRequest:
    """Canonical request for a generated media asset."""

    context: str = "generic"  # "slides", "pulse", etc.
    title: str = ""
    bullets: List[str] = field(default_factory=list)
    visual_context: str = ""
    category: str = ""
    topic: str = ""
    theme: str = ""
    tone: str = ""
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    output_path: Optional[str] = None
    prefer_gpu: bool = True
    seed: Optional[int] = None
    preferred_providers: List[str] = field(default_factory=list)
    # Structured data payloads — used by viz_kernel provider when available.
    chart_json: Optional[Dict[str, Any]] = None
    table_json: Optional[Dict[str, Any]] = None
    diagram_json: Optional[Dict[str, Any]] = None
    kpis_json: Optional[Dict[str, Any]] = None


def is_air_gap_media_mode() -> bool:
    """Return True when media generation should avoid cloud/external providers.

    Triggers on:
      - SQLite/non-PostgreSQL backend (typical air-gap/localhost setup)
      - ICDEV_AIRGAP_MEDIA=1/true/yes
      - Absence of any configured cloud API key
    """
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
    if backend != "postgresql":
        return True
    if os.environ.get("ICDEV_AIRGAP_MEDIA", "").lower() in ("1", "true", "yes"):
        return True
    cloud_keys = ["OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"]
    if not any(os.environ.get(k) for k in cloud_keys):
        return True
    return False


def _gpu_available() -> bool:
    """Check whether a CUDA GPU with >= 6 GB VRAM is present."""
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        props = torch.cuda.get_device_properties(0)
        total = getattr(props, "total_memory", None) or getattr(props, "total_mem", 0)
        return total >= 6 * 1024**3
    except Exception:
        return False


def _available_providers() -> List[str]:
    """Providers available on this machine right now."""
    providers: List[str] = ["slides_svg", "slides_matplotlib"]
    if _gpu_available():
        providers.insert(0, "pulse_sdxl")
    # viz_kernel is reserved for the tools/viz/ branch; it will be added once that
    # rendering kernel is present in the tree.
    return providers


def _load_providers_config() -> Dict[str, Any]:
    """Load provider priority overrides from args/media_providers.yaml if present."""
    config_path = Path(__file__).resolve().parents[3] / "args" / "media_providers.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception:
        return {}


def _configured_priority() -> List[str]:
    """Return provider priority from config (if any)."""
    cfg = _load_providers_config()
    providers = cfg.get("providers", {})
    priority = providers.get("priority", [])
    if isinstance(priority, str):
        return [p.strip() for p in priority.split(",") if p.strip()]
    return [str(p) for p in priority if isinstance(p, str)]


def _cache_key(req: AssetRequest) -> str:
    """Deterministic 16-char cache key from request inputs."""
    h = hashlib.sha256()
    h.update(req.context.encode("utf-8"))
    h.update(req.title.encode("utf-8"))
    h.update(";".join(req.bullets).encode("utf-8"))
    h.update(req.visual_context.encode("utf-8"))
    h.update(req.category.encode("utf-8"))
    h.update(req.topic.encode("utf-8"))
    h.update(req.theme.encode("utf-8"))
    h.update(req.tone.encode("utf-8"))
    h.update(f"{req.width}x{req.height}".encode("utf-8"))
    for payload in (req.chart_json, req.table_json, req.diagram_json, req.kpis_json):
        if payload:
            h.update(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:16]


def _cache_path(key: str, ext: str, output_dir: Path) -> Path:
    return output_dir / f"asset_{key}.{ext}"


class AssetGenerator:
    """Unified dispatcher for ICDEV-native media assets."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or DEFAULT_IMAGE_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, req: AssetRequest) -> Dict[str, Any]:
        """Generate an asset, trying providers in priority order with caching."""
        start = time.time()
        cache_key = _cache_key(req)
        cached = self._check_cache(cache_key)
        if cached:
            cached["elapsed_ms"] = int((time.time() - start) * 1000)
            return cached

        providers = self._select_providers(req)
        last_error: Optional[str] = None
        for provider in providers:
            try:
                result = self._run_provider(provider, req, cache_key)
                if result and result.get("success"):
                    result["elapsed_ms"] = int((time.time() - start) * 1000)
                    return result
                if result and result.get("error"):
                    last_error = result["error"]
            except Exception as exc:
                last_error = str(exc)

        return {
            "success": False,
            "error": last_error or "all providers failed",
            "method": "none",
            "elapsed_ms": int((time.time() - start) * 1000),
        }

    def _check_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        for ext in ("png", "svg"):
            path = _cache_path(cache_key, ext, self.output_dir)
            if path.exists():
                return {"success": True, "path": str(path), "method": f"cache:{ext}", "cached": True}
        return None

    def _select_providers(self, req: AssetRequest) -> List[str]:
        """Resolve provider order: request → config → air-gap → availability."""
        available = _available_providers()
        candidates: List[str] = []

        # 1. Request override
        for p in req.preferred_providers:
            if p not in candidates:
                candidates.append(p)

        # 2. Configured priority
        for p in _configured_priority():
            if p not in candidates:
                candidates.append(p)

        # 3. Runtime availability
        for p in available:
            if p not in candidates:
                candidates.append(p)

        # 4. Air-gap filter
        if is_air_gap_media_mode():
            candidates = [p for p in candidates if p in NATIVE_PROVIDERS]

        # Ensure at least SVG is present
        if "slides_svg" not in candidates:
            candidates.append("slides_svg")

        return candidates

    def _run_provider(self, provider: str, req: AssetRequest, cache_key: str) -> Dict[str, Any]:
        if provider == "pulse_sdxl":
            return self._provider_sdxl(req, cache_key)
        if provider == "slides_matplotlib":
            return self._provider_matplotlib(req, cache_key)
        if provider == "slides_svg":
            return self._provider_svg(req, cache_key)
        if provider == "viz_kernel":
            return self._provider_viz_kernel(req, cache_key)
        return {"success": False, "error": f"unknown provider {provider}", "method": provider}

    # ── Provider: SDXL Turbo ─────────────────────────────────────────────────

    def _provider_sdxl(self, req: AssetRequest, cache_key: str) -> Dict[str, Any]:
        start = time.time()
        prompt = _build_image_prompt(req.title, req.category, req.topic)

        width = (req.width // 8) * 8
        height = (req.height // 8) * 8
        output_path = self._resolve_output_path(req, cache_key, "png")

        try:
            import torch
            from diffusers import AutoPipelineForText2Image

            global _pipeline_cache
            if _pipeline_cache is None:
                dtype = torch.float16 if torch.cuda.is_available() else torch.float32
                device = "cuda" if torch.cuda.is_available() else "cpu"
                pipe = AutoPipelineForText2Image.from_pretrained(
                    SDXL_TURBO_MODEL_ID,
                    torch_dtype=dtype,
                    variant="fp16" if dtype == torch.float16 else None,
                )
                pipe = pipe.to(device)
                if hasattr(pipe, "enable_attention_slicing"):
                    pipe.enable_attention_slicing()
                _pipeline_cache = pipe

            generator = None
            seed = req.seed
            if seed is not None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                generator = torch.Generator(device=device).manual_seed(seed)
            else:
                seed = int.from_bytes(os.urandom(4), "big")

            result = _pipeline_cache(
                prompt=prompt,
                num_inference_steps=DEFAULT_STEPS,
                guidance_scale=DEFAULT_GUIDANCE,
                width=width,
                height=height,
                generator=generator,
            )
            image = result.images[0]
            image.save(output_path, "PNG")

            return {
                "success": True,
                "path": output_path,
                "method": "pulse_sdxl",
                "prompt": prompt,
                "seed": seed,
                "width": width,
                "height": height,
                "elapsed_ms": int((time.time() - start) * 1000),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "method": "pulse_sdxl", "prompt": prompt}

    # ── Provider: matplotlib ───────────────────────────────────────────────────

    def _provider_matplotlib(self, req: AssetRequest, cache_key: str) -> Dict[str, Any]:
        start = time.time()
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            import numpy as np
        except Exception as exc:
            return {"success": False, "error": f"matplotlib unavailable: {exc}", "method": "slides_matplotlib"}

        try:
            palette = _resolve_theme_palette(req.theme)
            bg_hex = _rgb_to_hex(palette["bg"])
            accent_hex = _rgb_to_hex(palette["accent"])
            text_hex = _rgb_to_hex(palette["text"])

            fig, ax = plt.subplots(figsize=(req.width / 100, req.height / 100), facecolor=bg_hex)
            ax.set_facecolor(bg_hex)
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 6)
            ax.axis("off")

            # Accent bar
            ax.add_patch(mpatches.FancyBboxPatch((0, 5.7), 10, 0.3, boxstyle="square,pad=0", facecolor=accent_hex, linewidth=0))

            # Abstract background shapes seeded from title
            rng = np.random.default_rng(abs(hash(req.title)) % (2**31))
            for _ in range(6):
                circle = plt.Circle(
                    (rng.uniform(1, 9), rng.uniform(0.5, 5)),
                    rng.uniform(0.3, 1.2),
                    color=accent_hex,
                    alpha=rng.uniform(0.05, 0.2),
                )
                ax.add_patch(circle)

            pts = [(rng.uniform(2, 8), rng.uniform(1, 4)) for _ in range(5)]
            for i in range(len(pts) - 1):
                ax.plot([pts[i][0], pts[i + 1][0]], [pts[i][1], pts[i + 1][1]], color=accent_hex, alpha=0.3, linewidth=1.5)
            for px, py in pts:
                ax.plot(px, py, "o", color=accent_hex, markersize=8, alpha=0.7)

            short_title = req.title[:50] + ("…" if len(req.title) > 50 else "")
            ax.text(
                5, 2.5, short_title, ha="center", va="center",
                color=text_hex, fontsize=13, fontweight="bold", wrap=True, family="monospace",
            )

            output_path = self._resolve_output_path(req, cache_key, "png")
            fig.savefig(output_path, format="png", dpi=96, bbox_inches="tight", facecolor=bg_hex)
            plt.close(fig)

            return {
                "success": True,
                "path": output_path,
                "method": "slides_matplotlib",
                "elapsed_ms": int((time.time() - start) * 1000),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "method": "slides_matplotlib"}

    # ── Provider: SVG ──────────────────────────────────────────────────────────

    def _provider_svg(self, req: AssetRequest, cache_key: str) -> Dict[str, Any]:
        start = time.time()
        try:
            title = req.title or "ICDEV™"
            category = req.category or req.topic or ""
            width = req.width
            height = req.height

            h = hashlib.md5(title.encode("utf-8"), usedforsecurity=False).hexdigest()
            hue1 = int(h[:3], 16) % 360
            hue2 = (hue1 + 40) % 360
            accent = f"hsl({(hue1 + 180) % 360}, 70%, 85%)"

            display_title = (title[:80] + ("..." if len(title) > 80 else "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            category_display = (category[:30].upper().replace("&", "&amp;") if category else "ICDEV™ ASSET")

            svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:hsl({hue1},65%,35%)"/>
      <stop offset="100%" style="stop-color:hsl({hue2},55%,50%)"/>
    </linearGradient>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="{accent}" stroke-width="0.5" opacity="0.15"/>
    </pattern>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect width="{width}" height="{height}" fill="url(#grid)"/>
  <rect x="60" y="{height // 2 - 80}" width="{width - 120}" height="160" rx="12" fill="rgba(0,0,0,0.3)"/>
  <text x="{width // 2}" y="{height // 2 - 20}" text-anchor="middle"
        font-family="system-ui, -apple-system, sans-serif" font-size="14"
        font-weight="600" letter-spacing="3" fill="{accent}">
    {category_display}
  </text>
  <text x="{width // 2}" y="{height // 2 + 25}" text-anchor="middle"
        font-family="system-ui, -apple-system, sans-serif" font-size="28"
        font-weight="700" fill="white">
    {display_title}
  </text>
  <text x="{width // 2}" y="{height - 30}" text-anchor="middle"
        font-family="system-ui, -apple-system, sans-serif" font-size="12"
        fill="rgba(255,255,255,0.5)">
    ICDEV™ Intelligent Certified Development
  </text>
</svg>"""
            output_path = self._resolve_output_path(req, cache_key, "svg")
            Path(output_path).write_text(svg, encoding="utf-8")

            return {
                "success": True,
                "path": output_path,
                "method": "slides_svg",
                "elapsed_ms": int((time.time() - start) * 1000),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "method": "slides_svg"}

    # ── Provider: viz_kernel (placeholder) ─────────────────────────────────────

    def _provider_viz_kernel(self, req: AssetRequest, cache_key: str) -> Dict[str, Any]:
        """Reserved for tools/viz/ rendering kernel integration.

        Once the VIZ branch (irad/feature) merges, this provider will render
        chart_json/table_json/diagram_json/kpis_json into PNG/SVG deterministically.
        """
        if not any((req.chart_json, req.table_json, req.diagram_json, req.kpis_json)):
            return {"success": False, "error": "no structured viz payload", "method": "viz_kernel"}
        return {"success": False, "error": "viz_kernel not yet available on this branch", "method": "viz_kernel"}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_output_path(self, req: AssetRequest, cache_key: str, ext: str) -> str:
        if req.output_path:
            return req.output_path
        return str(_cache_path(cache_key, ext, self.output_dir))


# ── Public convenience APIs ────────────────────────────────────────────────────


def generate_for_slide(
    title: str,
    bullets: Optional[List[str]] = None,
    visual_context: str = "",
    theme: str = "",
    tone: str = "",
    output_path: Optional[str] = None,
    preferred_providers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate a slide graphic using the unified native dispatcher."""
    req = AssetRequest(
        context="slides",
        title=title,
        bullets=bullets or [],
        visual_context=visual_context,
        theme=theme,
        tone=tone,
        output_path=output_path,
        preferred_providers=preferred_providers or [],
    )
    return AssetGenerator().generate(req)


def generate_hero_image(
    title: str,
    category: str = "",
    topic: str = "",
    output_path: Optional[str] = None,
    prefer_gpu: bool = True,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate a Pulse-style hero image using the unified native dispatcher."""
    req = AssetRequest(
        context="pulse",
        title=title,
        category=category,
        topic=topic,
        width=width,
        height=height,
        output_path=output_path,
        prefer_gpu=prefer_gpu,
        seed=seed,
    )
    return AssetGenerator().generate(req)


# ── Internal prompt/theme utilities ──────────────────────────────────────────


def _build_image_prompt(title: str, category: str = "", topic: str = "") -> str:
    """Build a topic-aware SDXL prompt from title/category/topic."""
    combined = f"{title} {category} {topic}".lower()
    matches = []
    for keyword, visual in _TOPIC_VISUAL_MAP.items():
        if keyword in combined:
            matches.append((len(keyword), keyword, visual))
    matches.sort(key=lambda x: x[0], reverse=True)

    if matches:
        scene = matches[0][2]
        h = hashlib.md5(title.encode("utf-8"), usedforsecurity=False).hexdigest()
        style_idx = int(h[:2], 16) % 5
        styles = [
            "photorealistic digital art, cinematic lighting, detailed, high quality, 4k, dark blue and teal color palette",
            "isometric 3D illustration, clean geometric shapes, soft studio lighting, high quality, 4k, purple and blue color palette",
            "futuristic concept art, neon accents, volumetric lighting, high quality, 4k, cyan and dark navy color palette",
            "minimalist flat design illustration, bold colors, clean lines, modern, high quality, 4k, blue and orange accent palette",
            "sci-fi movie poster style, dramatic lighting, depth of field, high quality, 4k, dark teal and gold accent palette",
        ]
        return f"{styles[style_idx]}, {scene}"

    style = (
        "professional digital illustration, modern technology concept art, corporate tech visual, "
        "clean composition, cinematic lighting, high quality, 4k, sharp focus, dark blue color palette"
    )
    return f"{style}, visual representation of: {title[:100]}"


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _resolve_theme_palette(theme: str) -> Dict[str, tuple[int, int, int]]:
    """Resolve a theme name to a palette dict with bg/accent/text keys."""
    try:
        from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME

        return THEME_PALETTES.get(theme) or THEME_PALETTES[DEFAULT_THEME]
    except Exception:
        return {
            "bg": (0x0A, 0x16, 0x28),
            "accent": (0xC8, 0xA9, 0x51),
            "text": (0xFF, 0xFF, 0xFF),
            "subtext": (0xE0, 0xE6, 0xF0),
            "dark": (0x1E, 0x3A, 0x5F),
        }
