# CUI // SP-CTI
"""Pulse hero image generator — local GPU-accelerated blog image generation.

Uses Stable Diffusion XL Turbo (stabilityai/sdxl-turbo) for fast, free,
offline image generation on consumer GPUs (8 GB VRAM sufficient).

Two-tier approach (D-WG-2 pattern):
  Tier 1 (Deterministic): Template-based SVG fallback — zero deps, air-gap safe
  Tier 2 (GPU):           SDXL Turbo via diffusers — requires torch + CUDA GPU

Called by:
    tools/genesis/reflexes/publish.py  (Genesis Publish Reflex)
    tools/pulse/engine/scheduler.py    (Pulse pipeline)

CLI:
    python tools/pulse/engine/image_generator.py --prompt "text" --output path.png
    python tools/pulse/engine/image_generator.py --prompt "text" --output path.png --json
    python tools/pulse/engine/image_generator.py --health --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Default storage directory for generated images
DEFAULT_IMAGE_DIR = BASE_DIR / "data" / "pulse" / "images"

# Model ID for HuggingFace Hub download
SDXL_TURBO_MODEL_ID = "stabilityai/sdxl-turbo"

# Image defaults
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 576  # 16:9 ratio — ideal for blog hero banners
DEFAULT_STEPS = 4  # SDXL Turbo is designed for 1-4 steps
DEFAULT_GUIDANCE = 0.0  # SDXL Turbo uses CFG=0 (guidance-free)


# ---------------------------------------------------------------------------
# GPU / CUDA detection
# ---------------------------------------------------------------------------


def check_gpu() -> Dict[str, Any]:
    """Check GPU availability and VRAM for image generation."""
    result = {
        "cuda_available": False,
        "device_name": None,
        "vram_total_gb": 0.0,
        "vram_free_gb": 0.0,
        "sdxl_turbo_compatible": False,
    }
    try:
        import torch

        if torch.cuda.is_available():
            result["cuda_available"] = True
            result["device_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            total = getattr(props, "total_memory", None) or getattr(props, "total_mem", 0)
            free = total - torch.cuda.memory_allocated(0)
            result["vram_total_gb"] = round(total / (1024**3), 1)
            result["vram_free_gb"] = round(free / (1024**3), 1)
            # SDXL Turbo needs ~6 GB VRAM
            result["sdxl_turbo_compatible"] = result["vram_total_gb"] >= 6.0
    except ImportError:
        pass
    return result


# ---------------------------------------------------------------------------
# Tier 1: Deterministic SVG fallback (zero deps, air-gap safe)
# ---------------------------------------------------------------------------


def _generate_svg_hero(
    title: str,
    category: str = "",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> str:
    """Generate a branded SVG hero image from article metadata.

    Returns SVG string. Deterministic — same inputs produce same output.
    """
    # Derive a consistent color from title hash
    h = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()
    hue1 = int(h[:3], 16) % 360
    hue2 = (hue1 + 40) % 360
    color1 = f"hsl({hue1}, 65%, 35%)"
    color2 = f"hsl({hue2}, 55%, 50%)"
    accent = f"hsl({(hue1 + 180) % 360}, 70%, 85%)"

    # Truncate title for display
    display_title = title[:80] + ("..." if len(title) > 80 else "")
    # Escape XML entities
    display_title = display_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    category_display = category[:30].upper().replace("&", "&amp;") if category else "ICDEV™ PULSE"

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:{color1}"/>
      <stop offset="100%" style="stop-color:{color2}"/>
    </linearGradient>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="{accent}" stroke-width="0.5" opacity="0.15"/>
    </pattern>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect width="{width}" height="{height}" fill="url(#grid)"/>
  <rect x="60" y="{height // 2 - 80}" width="{width - 120}" height="160" rx="12"
        fill="rgba(0,0,0,0.3)"/>
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
    return svg


def generate_svg(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Dict[str, Any]:
    """Generate a deterministic SVG hero image (Tier 1 fallback).

    Parameters
    ----------
    title : str
        Article title for text overlay and color derivation.
    category : str
        Optional category label shown above title.
    output_path : str or None
        File path for output. Auto-generated if None.
    width, height : int
        Image dimensions.

    Returns
    -------
    dict
        ``{"success": bool, "path": str, "method": "svg", "elapsed_ms": int}``
    """
    start = time.time()

    svg_content = _generate_svg_hero(title, category, width, height)

    if not output_path:
        slug = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()[:12]
        DEFAULT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(DEFAULT_IMAGE_DIR / f"hero-{slug}.svg")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(svg_content, encoding="utf-8")

    elapsed = int((time.time() - start) * 1000)
    return {
        "success": True,
        "path": output_path,
        "method": "svg",
        "width": width,
        "height": height,
        "elapsed_ms": elapsed,
    }


# ---------------------------------------------------------------------------
# Tier 2: SDXL Turbo GPU generation (requires torch + diffusers)
# ---------------------------------------------------------------------------

# Module-level pipeline cache — loaded once per process
_pipeline_cache = None


def _get_pipeline():
    """Lazy-load SDXL Turbo pipeline (cached after first call)."""
    global _pipeline_cache
    if _pipeline_cache is not None:
        return _pipeline_cache

    import torch
    from diffusers import AutoPipelineForText2Image

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    device = "cuda" if torch.cuda.is_available() else "cpu"

    pipe = AutoPipelineForText2Image.from_pretrained(
        SDXL_TURBO_MODEL_ID,
        torch_dtype=dtype,
        variant="fp16" if dtype == torch.float16 else None,
    )
    pipe = pipe.to(device)

    # Memory optimizations for 8 GB VRAM
    if hasattr(pipe, "enable_attention_slicing"):
        pipe.enable_attention_slicing()

    _pipeline_cache = pipe
    return pipe


_TOPIC_VISUAL_MAP = {
    # Map domain keywords to concrete visual elements SDXL can render
    "compliance": "government building with digital shield overlay, regulatory documents, checkmarks on screens",
    "ato": "security authorization dashboard with green status indicators, federal seal, compliance checklist on screen",
    "fedramp": "cloud infrastructure with security shield, federal compliance certification badges on monitors",
    "cmmc": "military defense network with layered security barriers, certification badge",
    "zero trust": "network diagram with locked nodes and encrypted connections, no perimeter, identity verification at every point",
    "devsecops": "software development pipeline with security gates, code on screens with shield icons, CI/CD flow",
    "agentic": "multiple AI robot agents collaborating around a holographic blueprint, building architecture together",
    "vibe coding": "developer at desk with AI code floating in air, warning symbols on some code blocks, quality gates blocking bad code",
    "security": "cybersecurity operations center with threat dashboards, lock icons, network monitoring screens",
    "sbom": "software supply chain diagram with component boxes, dependency tree visualization, vulnerability scan results",
    "cato": "continuous monitoring dashboard with real-time compliance gauges, green/yellow/red status lights, evidence timeline",
    "modernization": "legacy mainframe transforming into modern cloud microservices, digital transformation visual",
    "kubernetes": "container orchestration dashboard, pod status indicators, service mesh visualization",
    "terraform": "infrastructure as code, cloud architecture diagram being generated from code",
    "ai": "artificial intelligence neural network visualization, machine learning workflow, data processing pipeline",
    "embedded": "circuit board with microcontroller, IoT sensor network, firmware deployment",
    "blockchain": "distributed ledger with connected nodes, cryptographic chain links, consensus visualization",
    # Financial / Market Intelligence
    "market intelligence": "stock market trading terminal with candlestick charts, green and red price bars, financial data screens showing tickers and volume",
    "trading": "stock market trading terminal with candlestick charts, green and red price bars, financial data screens, multiple monitors",
    "market": "financial trading floor with multiple monitors showing stock tickers, price charts, and market data heatmaps",
    "stock": "wall street trading screens with candlestick charts, stock price tickers, volume bars, financial dashboard",
    "finance": "financial analytics dashboard with charts, graphs, portfolio metrics, and real-time market data feeds",
    "portfolio": "investment portfolio dashboard with asset allocation pie charts, performance graphs, risk metrics on screens",
    "signal": "trading signal dashboard with buy and sell indicators, confidence scores, alert notifications on dark screens",
    "scenario": "what-if scenario simulation dashboard with cascade flow diagrams, impact analysis charts, decision trees",
    "knowledge graph": "interactive network graph visualization with connected nodes and edges, relationship mapping, entity connections",
    # Intelligence / Research
    "intelligence": "intelligence operations center with multiple data feeds, analysis dashboards, correlation maps on large screens",
    "research": "research lab with data visualization dashboards, trend analysis charts, scientific publication graphs",
    "proposal": "government proposal review workspace with compliance checklists, document sections, evaluation matrices on screens",
    "govcon": "government contracting office with procurement data on screens, proposal volumes, compliance matrices",
    # Federal bridge domain topics
    "cloud migration": "hybrid cloud migration diagram with on-premise servers connecting to cloud, data flowing between environments, security checkpoints",
    "cloud": "cloud computing infrastructure with virtual servers, load balancers, security groups, multi-region architecture diagram",
    "data integration": "enterprise data integration hub with ETL pipelines, database connections, API gateways, unified data flowing between systems",
    "program management": "digital program management dashboard with Gantt charts, milestones, resource allocation, earned value metrics on screens",
    "airspace": "air traffic control center with radar displays, flight path visualizations, command and control systems, digital airspace map",
    "healthcare": "healthcare IT system with electronic health records dashboard, patient data integration, HL7 FHIR data streams on medical monitors",
    "encryption": "cryptographic key exchange diagram with encrypted data streams, cipher blocks, hardware security modules, digital locks",
    "quantum": "quantum computing processor with qubit visualization, post-quantum cryptography lattice structures, quantum-safe key exchange",
    "wayfinding": "digital wayfinding system with interactive maps, indoor navigation, beacon network, touchscreen kiosk displays",
    "network": "enterprise network architecture diagram with routers, firewalls, VPN tunnels, network segmentation, traffic monitoring",
    "mission critical": "mission operations center with real-time system monitoring, redundant displays, alert dashboards, failover indicators",
    "logistics": "military logistics command center with supply chain maps, inventory dashboards, distribution network visualization",
}


def _build_image_prompt(title: str, category: str = "", topic: str = "") -> str:
    """Build a topic-aware image generation prompt from article metadata.

    Maps article topics to concrete visual elements that SDXL Turbo
    can render effectively, rather than generic abstract art.

    Parameters
    ----------
    title : str
        Article title.
    category : str
        Optional category or cluster name.
    topic : str
        Optional topic description or keywords for richer matching.
    """
    # Combine all available text for keyword matching
    combined = f"{title} {category} {topic}".lower()
    # Collect matches with keyword length as priority (longer = more specific)
    matches = []
    for keyword, visual in _TOPIC_VISUAL_MAP.items():
        if keyword in combined:
            matches.append((len(keyword), keyword, visual))
    # Sort by keyword length descending so most specific match wins
    matches.sort(key=lambda x: x[0], reverse=True)
    visuals = [v for _, _, v in matches]

    if visuals:
        # Use the most specific matching visual
        scene = visuals[0]
        # Vary the art style based on title hash for diversity
        h = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()
        style_idx = int(h[:2], 16) % 5
        styles = [
            "photorealistic digital art, cinematic lighting, detailed, high quality, 4k, dark blue and teal color palette",
            "isometric 3D illustration, clean geometric shapes, soft studio lighting, high quality, 4k, purple and blue color palette",
            "futuristic concept art, neon accents, volumetric lighting, high quality, 4k, cyan and dark navy color palette",
            "minimalist flat design illustration, bold colors, clean lines, modern, high quality, 4k, blue and orange accent palette",
            "sci-fi movie poster style, dramatic lighting, depth of field, high quality, 4k, dark teal and gold accent palette",
        ]
        style = styles[style_idx]
        prompt = f"{style}, {scene}"
    else:
        # Fallback: use title directly with professional style
        style = (
            "professional digital illustration, "
            "modern technology concept art, corporate tech visual, "
            "clean composition, cinematic lighting, "
            "high quality, 4k, sharp focus, dark blue color palette"
        )
        prompt = f"{style}, visual representation of: {title[:100]}"

    return prompt


def generate_image(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    prompt_override: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    steps: int = DEFAULT_STEPS,
    guidance_scale: float = DEFAULT_GUIDANCE,
    seed: Optional[int] = None,
    topic: str = "",
) -> Dict[str, Any]:
    """Generate a hero image using SDXL Turbo on local GPU.

    Parameters
    ----------
    title : str
        Article title — used to auto-build the image prompt.
    category : str
        Optional category for prompt context.
    output_path : str or None
        File path for output PNG. Auto-generated if None.
    prompt_override : str or None
        Override the auto-generated prompt entirely.
    width, height : int
        Image dimensions (must be divisible by 8).
    steps : int
        Inference steps (1-4 for SDXL Turbo, default 4).
    guidance_scale : float
        CFG guidance (0.0 for SDXL Turbo, which is guidance-free).
    seed : int or None
        Random seed for reproducibility. None for random.
    topic : str
        Optional topic or keywords for richer prompt matching.

    Returns
    -------
    dict
        ``{"success": bool, "path": str, "method": "sdxl_turbo",
          "prompt": str, "seed": int, "elapsed_ms": int}``
    """
    start = time.time()

    # Validate dimensions are divisible by 8
    width = (width // 8) * 8
    height = (height // 8) * 8

    prompt = prompt_override or _build_image_prompt(title, category, topic)

    # Build output path
    if not output_path:
        slug = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()[:12]
        DEFAULT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(DEFAULT_IMAGE_DIR / f"hero-{slug}.png")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        import torch

        pipe = _get_pipeline()

        generator = None
        if seed is not None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            generator = torch.Generator(device=device).manual_seed(seed)
        else:
            seed = int.from_bytes(os.urandom(4), "big")

        result = pipe(
            prompt=prompt,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
            generator=generator,
        )

        image = result.images[0]
        image.save(output_path, "PNG")

        elapsed = int((time.time() - start) * 1000)

        return {
            "success": True,
            "path": output_path,
            "method": "sdxl_turbo",
            "prompt": prompt,
            "seed": seed,
            "width": width,
            "height": height,
            "steps": steps,
            "elapsed_ms": elapsed,
        }

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {
            "success": False,
            "error": str(e),
            "method": "sdxl_turbo",
            "prompt": prompt,
            "elapsed_ms": elapsed,
        }


# ---------------------------------------------------------------------------
# Public API: auto-select best available method
# ---------------------------------------------------------------------------


def generate_hero_image(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    prefer_gpu: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """Generate a hero image for a Pulse article, auto-selecting the best method.

    Tries GPU generation first (SDXL Turbo), falls back to SVG template.

    Parameters
    ----------
    title : str
        Article title.
    category : str
        Optional category label.
    output_path : str or None
        Output file path. Auto-generated if None.
    prefer_gpu : bool
        If True (default), attempt GPU generation before SVG fallback.
    **kwargs
        Passed through to generate_image() (seed, steps, width, height, etc.)

    Returns
    -------
    dict
        ``{"success": bool, "path": str, "method": str, ...}``
    """
    if not title:
        return {"success": False, "error": "empty title"}

    if prefer_gpu:
        gpu = check_gpu()
        if gpu["sdxl_turbo_compatible"]:
            result = generate_image(
                title=title,
                category=category,
                output_path=output_path,
                **kwargs,
            )
            if result.get("success"):
                return result
            # Fall through to SVG on failure

    # Tier 1 fallback: deterministic SVG
    svg_path = output_path
    if svg_path and svg_path.endswith(".png"):
        svg_path = svg_path.rsplit(".", 1)[0] + ".svg"
    return generate_svg(
        title=title,
        category=category,
        output_path=svg_path,
        width=kwargs.get("width", DEFAULT_WIDTH),
        height=kwargs.get("height", DEFAULT_HEIGHT),
    )


# ---------------------------------------------------------------------------
# Pipeline compatibility alias (called by scheduler.py)
# ---------------------------------------------------------------------------


def create_post_image(title: str, topic: str = "") -> dict:
    """Generate a hero image for the Pulse pipeline.

    Alias for generate_hero_image() matching the scheduler's expected API.
    Returns dict with 'path', 'url', 'method'.
    """
    result = generate_hero_image(title=title, category=topic, topic=topic)
    # Normalize to scheduler's expected keys
    result["url"] = result.get("path", "")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Pulse hero image generator (SDXL Turbo / SVG fallback)")
    parser.add_argument("--prompt", type=str, help="Article title or custom prompt")
    parser.add_argument("--category", type=str, default="", help="Article category")
    parser.add_argument("--output", type=str, help="Output file path")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--svg-only", action="store_true", help="Force SVG (no GPU)")
    parser.add_argument("--gpu-only", action="store_true", help="Force GPU (no SVG fallback)")
    parser.add_argument("--health", action="store_true", help="Check GPU health")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = check_gpu()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"CUDA available:  {result['cuda_available']}")
            print(f"Device:          {result['device_name'] or 'N/A'}")
            print(f"VRAM total:      {result['vram_total_gb']} GB")
            print(f"VRAM free:       {result['vram_free_gb']} GB")
            print(f"SDXL compatible: {result['sdxl_turbo_compatible']}")
        return

    if not args.prompt:
        parser.error("--prompt is required (unless --health)")

    if args.svg_only:
        result = generate_svg(
            title=args.prompt,
            category=args.category,
            output_path=args.output,
            width=args.width,
            height=args.height,
        )
    elif args.gpu_only:
        result = generate_image(
            title=args.prompt,
            category=args.category,
            output_path=args.output,
            width=args.width,
            height=args.height,
            steps=args.steps,
            seed=args.seed,
        )
    else:
        result = generate_hero_image(
            title=args.prompt,
            category=args.category,
            output_path=args.output,
            width=args.width,
            height=args.height,
            steps=args.steps,
            seed=args.seed,
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("success"):
            print(f"Generated: {result['path']}")
            print(f"Method:    {result['method']}")
            print(f"Elapsed:   {result.get('elapsed_ms', 0)} ms")
            if result.get("prompt"):
                print(f"Prompt:    {result['prompt'][:100]}...")
        else:
            print(f"FAILED: {result.get('error', 'unknown')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
