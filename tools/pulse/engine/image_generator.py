# CUI // SP-CTI
"""Pulse hero image generator — ICDEV-native unified dispatcher wrapper.

Thin wrapper around tools.viz.asset_generator that preserves the existing
Pulse API (generate_image, generate_svg, generate_hero_image, create_post_image,
check_gpu, CLI).  The actual generation is handled by the shared native dispatcher:
  - pulse_sdxl      local SDXL Turbo GPU generation
  - slides_matplotlib  programmatic fallback
  - slides_svg      deterministic zero-dependency fallback
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.viz.asset_generator import (
    AssetGenerator,
    AssetRequest,
    check_gpu as asset_check_gpu,
)

# Default storage directory for generated images
DEFAULT_IMAGE_DIR = BASE_DIR / "data" / "pulse" / "images"

# Image defaults (mirrored from asset_generator defaults)
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 576
DEFAULT_STEPS = 4
DEFAULT_GUIDANCE = 0.0


def check_gpu() -> Dict[str, Any]:
    """Check GPU availability and VRAM for image generation."""
    return asset_check_gpu()


def generate_svg(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Dict[str, Any]:
    """Generate a deterministic SVG hero image (Tier 1 fallback)."""
    start = time.time()
    req = AssetRequest(
        context="pulse",
        title=title,
        category=category,
        width=width,
        height=height,
        output_path=output_path,
        preferred_providers=["slides_svg"],
    )
    result = AssetGenerator(output_dir=DEFAULT_IMAGE_DIR).generate(req)
    result["elapsed_ms"] = int((time.time() - start) * 1000)
    return result


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
    """Generate a hero image using local SDXL Turbo on GPU."""
    start = time.time()
    req = AssetRequest(
        context="pulse",
        title=title,
        category=category,
        topic=topic,
        prompt=prompt_override or "",
        width=width,
        height=height,
        output_path=output_path,
        seed=seed,
        steps=steps,
        guidance_scale=guidance_scale,
        preferred_providers=["pulse_sdxl", "slides_svg"],
    )
    result = AssetGenerator(output_dir=DEFAULT_IMAGE_DIR).generate(req)
    result["elapsed_ms"] = int((time.time() - start) * 1000)
    return result


def generate_hero_image(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    prefer_gpu: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """Generate a hero image for a Pulse article, auto-selecting the best native method."""
    if not title:
        return {"success": False, "error": "empty title"}

    start = time.time()
    req = AssetRequest(
        context="pulse",
        title=title,
        category=category,
        topic=kwargs.get("topic", ""),
        width=kwargs.get("width", DEFAULT_WIDTH),
        height=kwargs.get("height", DEFAULT_HEIGHT),
        output_path=output_path,
        seed=kwargs.get("seed", None),
        steps=kwargs.get("steps", DEFAULT_STEPS),
        guidance_scale=kwargs.get("guidance_scale", DEFAULT_GUIDANCE),
        prefer_gpu=prefer_gpu,
    )
    result = AssetGenerator(output_dir=DEFAULT_IMAGE_DIR).generate(req)
    result["elapsed_ms"] = int((time.time() - start) * 1000)
    return result


def create_post_image(title: str, topic: str = "") -> dict:
    """Generate a hero image for the Pulse pipeline.

    Alias for generate_hero_image() matching the scheduler's expected API.
    Returns dict with 'path', 'url', 'method'.
    """
    result = generate_hero_image(title=title, category=topic, topic=topic)
    result["url"] = result.get("path", "")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Pulse hero image generator (ICDEV-native)")
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

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
