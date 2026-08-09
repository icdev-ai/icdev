# CUI // SP-CTI
"""ICDEV™ Pulse video generator — local GPU-accelerated blog video generation.

Uses LTX-Video 2B (Lightricks ltxv-2b-0.9.8-distilled) for text-to-video
generation on consumer GPUs (RTX 4060 8 GB VRAM via int8 quantization).

Two-tier approach (D-WG-2 pattern):
  Tier 1 (Deterministic): Animated SVG fallback — zero deps, air-gap safe
  Tier 2 (GPU):           LTX-Video 2B via diffusers — requires torch + CUDA

Called by:
    tools/pulse/engine/scheduler.py  (Pulse pipeline)

CLI:
    python tools/pulse/engine/video_generator.py --prompt "text" --output path.mp4
    python tools/pulse/engine/video_generator.py --prompt "text" --output path.mp4 --json
    python tools/pulse/engine/video_generator.py --health --json
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

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# Default storage directory for generated videos
DEFAULT_VIDEO_DIR = BASE_DIR / "data" / "pulse" / "videos"

# LTX-Video 2B model (smallest, fits 8 GB VRAM with int8)
LTX_MODEL_ID = "Lightricks/LTX-Video"
LTX_MODEL_VARIANT = "ltxv-2b-0.9.8-distilled"

# Video defaults — tuned for RTX 4060 8 GB VRAM
DEFAULT_WIDTH = 704  # Divisible by 32
DEFAULT_HEIGHT = 480  # Divisible by 32, 16:9-ish
DEFAULT_FPS = 24
DEFAULT_NUM_FRAMES = 97  # Must be divisible by 8 + 1 (96+1=97), ~4s at 24fps
DEFAULT_STEPS = 20  # Inference steps (distilled model: 20 is good)
DEFAULT_GUIDANCE = 7.5  # CFG scale
MIN_VRAM_GB = 6.0  # Minimum VRAM for LTX-Video 2B


# ---------------------------------------------------------------------------
# GPU / CUDA detection
# ---------------------------------------------------------------------------


def check_gpu() -> Dict[str, Any]:
    """Check GPU availability and VRAM for video generation."""
    result = {
        "cuda_available": False,
        "device_name": None,
        "vram_total_gb": 0.0,
        "vram_free_gb": 0.0,
        "ltx_video_compatible": False,
        "ltx_video_installed": False,
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
            result["ltx_video_compatible"] = result["vram_total_gb"] >= MIN_VRAM_GB
    except ImportError:
        pass

    # Check if diffusers + LTX pipeline are installed
    try:
        from diffusers import DiffusionPipeline  # noqa: F401

        result["ltx_video_installed"] = True
    except ImportError:
        pass

    return result


# ---------------------------------------------------------------------------
# Topic → video prompt mapping
# ---------------------------------------------------------------------------

_TOPIC_VISUAL_MAP = {
    "compliance": "digital data flowing through security checkpoints with glowing green approval indicators",
    "ato": "holographic security dashboard displaying real-time compliance metrics with pulsing status lights",
    "fedramp": "cloud infrastructure with cascading security shields and federal certification seals",
    "cmmc": "layered defense network with animated security barriers activating sequentially",
    "zero trust": "network of locked nodes with identity verification beams scanning each connection",
    "devsecops": "software pipeline with code flowing through security gates that glow on pass",
    "agentic": "multiple AI agents collaborating around a central holographic workspace",
    "security": "cybersecurity command center with threat maps and scrolling security alerts",
    "sbom": "software supply chain visualization with component boxes linking and validating",
    "modernization": "legacy system transforming into modern cloud architecture with particle effects",
    "kubernetes": "container orchestration dashboard with pods spinning up and health checks pulsing",
    "ai": "neural network visualization with data flowing through interconnected layers",
    "embedded": "circuit board with signals flowing between microcontrollers and sensors",
}


def _build_video_prompt(title: str, category: str = "") -> str:
    """Build a topic-aware video generation prompt from article metadata."""
    combined = f"{title} {category}".lower()
    visuals = []
    for keyword, visual in _TOPIC_VISUAL_MAP.items():
        if keyword in combined:
            visuals.append(visual)

    if visuals:
        scene = visuals[0]
    else:
        scene = f"abstract technology visualization representing: {title[:80]}"

    # LTX-Video responds well to cinematic motion descriptions
    prompt = (
        f"Cinematic 4-second video loop, smooth camera movement, "
        f"professional technology visualization, {scene}, "
        f"dark blue and teal color palette, subtle particle effects, "
        f"high quality, sharp focus, ambient lighting"
    )
    return prompt


# ---------------------------------------------------------------------------
# Tier 1: Animated SVG fallback (zero deps, air-gap safe)
# ---------------------------------------------------------------------------


def generate_svg_video(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Dict[str, Any]:
    """Generate a branded animated SVG as video fallback.

    Returns an SVG with CSS animation — plays in browsers, zero deps.
    """
    start = time.time()

    h = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()
    hue1 = int(h[:3], 16) % 360
    hue2 = (hue1 + 40) % 360
    color1 = f"hsl({hue1}, 65%, 25%)"
    color2 = f"hsl({hue2}, 55%, 40%)"
    accent = f"hsl({(hue1 + 180) % 360}, 70%, 75%)"

    display_title = title[:60] + ("..." if len(title) > 60 else "")
    display_title = display_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    category_display = category[:30].upper().replace("&", "&amp;") if category else "ICDEV™ PULSE"

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"
     width="{width}" height="{height}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:{color1}"/>
      <stop offset="100%" style="stop-color:{color2}"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <!-- Animated grid lines -->
  <g opacity="0.15">
    <line x1="0" y1="{height // 3}" x2="{width}" y2="{height // 3}" stroke="{accent}" stroke-width="0.5">
      <animate attributeName="y1" values="{height // 3};{height // 3 + 20};{height // 3}" dur="4s" repeatCount="indefinite"/>
      <animate attributeName="y2" values="{height // 3};{height // 3 + 20};{height // 3}" dur="4s" repeatCount="indefinite"/>
    </line>
    <line x1="0" y1="{2 * height // 3}" x2="{width}" y2="{2 * height // 3}" stroke="{accent}" stroke-width="0.5">
      <animate attributeName="y1" values="{2 * height // 3};{2 * height // 3 - 20};{2 * height // 3}" dur="4s" repeatCount="indefinite"/>
      <animate attributeName="y2" values="{2 * height // 3};{2 * height // 3 - 20};{2 * height // 3}" dur="4s" repeatCount="indefinite"/>
    </line>
  </g>
  <!-- Pulsing circle -->
  <circle cx="{width // 2}" cy="{height // 2}" r="80" fill="none" stroke="{accent}" stroke-width="1" opacity="0.3">
    <animate attributeName="r" values="80;120;80" dur="4s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="0.3;0.1;0.3" dur="4s" repeatCount="indefinite"/>
  </circle>
  <!-- Content overlay -->
  <rect x="60" y="{height // 2 - 50}" width="{width - 120}" height="100" rx="10" fill="rgba(0,0,0,0.4)"/>
  <text x="{width // 2}" y="{height // 2 - 10}" text-anchor="middle"
        font-family="system-ui, sans-serif" font-size="12" font-weight="600"
        letter-spacing="2" fill="{accent}">
    {category_display}
  </text>
  <text x="{width // 2}" y="{height // 2 + 20}" text-anchor="middle"
        font-family="system-ui, sans-serif" font-size="20" font-weight="700" fill="white">
    {display_title}
  </text>
  <!-- Play icon hint -->
  <polygon points="{width // 2 - 12},{height - 50} {width // 2 - 12},{height - 30} {width // 2 + 8},{height - 40}"
           fill="rgba(255,255,255,0.4)">
    <animate attributeName="opacity" values="0.4;0.8;0.4" dur="2s" repeatCount="indefinite"/>
  </polygon>
</svg>"""

    if not output_path:
        slug = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()[:12]
        DEFAULT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(DEFAULT_VIDEO_DIR / f"video-{slug}.svg")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(svg, encoding="utf-8", newline="")

    elapsed = int((time.time() - start) * 1000)
    return {
        "success": True,
        "path": output_path,
        "method": "animated_svg",
        "width": width,
        "height": height,
        "duration_sec": 4.0,
        "elapsed_ms": elapsed,
    }


# ---------------------------------------------------------------------------
# Tier 2: LTX-Video 2B GPU generation (requires torch + diffusers)
# ---------------------------------------------------------------------------

_pipeline_cache = None


def _get_pipeline():
    """Lazy-load LTX-Video 2B pipeline (cached after first call)."""
    global _pipeline_cache
    if _pipeline_cache is not None:
        return _pipeline_cache

    import torch
    from diffusers import DiffusionPipeline

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    device = "cuda" if torch.cuda.is_available() else "cpu"

    pipe = DiffusionPipeline.from_pretrained(
        LTX_MODEL_ID,
        torch_dtype=dtype,
        variant="fp16" if dtype == torch.float16 else None,
    )
    pipe = pipe.to(device)

    # Memory optimizations for 8 GB VRAM
    if hasattr(pipe, "enable_model_cpu_offload"):
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pass
    if hasattr(pipe, "enable_attention_slicing"):
        pipe.enable_attention_slicing()

    _pipeline_cache = pipe
    return pipe


def generate_video(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    prompt_override: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    num_frames: int = DEFAULT_NUM_FRAMES,
    fps: int = DEFAULT_FPS,
    steps: int = DEFAULT_STEPS,
    guidance_scale: float = DEFAULT_GUIDANCE,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate a video clip using LTX-Video 2B on local GPU.

    Parameters
    ----------
    title : str
        Article title — used to auto-build the video prompt.
    category : str
        Optional category for prompt context.
    output_path : str or None
        File path for output MP4. Auto-generated if None.
    prompt_override : str or None
        Override the auto-generated prompt entirely.
    width, height : int
        Video dimensions (must be divisible by 32).
    num_frames : int
        Number of frames (must be divisible by 8 + 1).
    fps : int
        Frames per second for output video.
    steps : int
        Inference steps.
    guidance_scale : float
        CFG guidance scale.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    dict
        ``{"success": bool, "path": str, "method": "ltx_video_2b",
          "prompt": str, "seed": int, "elapsed_ms": int, "duration_sec": float}``
    """
    start = time.time()

    # Ensure dimensions are divisible by 32
    width = (width // 32) * 32
    height = (height // 32) * 32

    # Ensure num_frames is divisible by 8 + 1
    base = ((num_frames - 1) // 8) * 8
    num_frames = base + 1

    prompt = prompt_override or _build_video_prompt(title, category)

    if not output_path:
        slug = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()[:12]
        DEFAULT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(DEFAULT_VIDEO_DIR / f"video-{slug}.mp4")

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
            negative_prompt="blurry, low quality, distorted, text, watermark",
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
            num_frames=num_frames,
            generator=generator,
        )

        # Export frames to MP4
        frames = result.frames[0] if hasattr(result, "frames") else result.images
        _export_frames_to_mp4(frames, output_path, fps)

        duration_sec = round(num_frames / fps, 2)
        elapsed = int((time.time() - start) * 1000)

        return {
            "success": True,
            "path": output_path,
            "method": "ltx_video_2b",
            "prompt": prompt,
            "seed": seed,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "fps": fps,
            "duration_sec": duration_sec,
            "steps": steps,
            "elapsed_ms": elapsed,
        }

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        logger.error("LTX-Video generation failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "method": "ltx_video_2b",
            "prompt": prompt,
            "elapsed_ms": elapsed,
        }


def _export_frames_to_mp4(frames, output_path: str, fps: int = DEFAULT_FPS):
    """Export a list of PIL images or tensor frames to MP4.

    Tries imageio (ffmpeg) first, falls back to OpenCV.
    """
    import numpy as np

    # Convert frames to numpy arrays if needed
    np_frames = []
    for frame in frames:
        if hasattr(frame, "numpy"):
            # Tensor → numpy
            arr = frame.cpu().numpy()
            if arr.max() <= 1.0:
                arr = (arr * 255).astype(np.uint8)
            else:
                arr = arr.astype(np.uint8)
            if arr.ndim == 3 and arr.shape[0] in (3, 4):
                arr = arr.transpose(1, 2, 0)  # CHW → HWC
            np_frames.append(arr)
        elif hasattr(frame, "convert"):
            # PIL Image
            np_frames.append(np.array(frame))
        else:
            np_frames.append(np.array(frame, dtype=np.uint8))

    # Try imageio (preferred — uses ffmpeg)
    try:
        import imageio.v3 as iio

        iio.imwrite(output_path, np_frames, fps=fps, codec="libx264")
        return
    except ImportError:
        pass
    except Exception:
        pass

    try:
        import imageio

        writer = imageio.get_writer(output_path, fps=fps, codec="libx264")
        for f in np_frames:
            writer.append_data(f)
        writer.close()
        return
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: OpenCV
    try:
        import cv2

        h, w = np_frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
        for f in np_frames:
            if f.ndim == 3 and f.shape[2] == 3:
                f = cv2.cvtColor(f, cv2.COLOR_RGB2BGR)
            out.write(f)
        out.release()
        return
    except ImportError:
        pass

    raise RuntimeError("No video encoder available. Install imageio[ffmpeg] or opencv-python.")


# ---------------------------------------------------------------------------
# Public API: auto-select best available method
# ---------------------------------------------------------------------------


def generate_post_video(
    title: str,
    category: str = "",
    output_path: Optional[str] = None,
    prefer_gpu: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """Generate a video clip for a Pulse article, auto-selecting the best method.

    Tries GPU generation first (LTX-Video 2B), falls back to animated SVG.

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
        Passed through to generate_video().

    Returns
    -------
    dict
        ``{"success": bool, "path": str, "method": str, "duration_sec": float, ...}``
    """
    if not title:
        return {"success": False, "error": "empty title"}

    if prefer_gpu:
        gpu = check_gpu()
        if gpu["ltx_video_compatible"] and gpu["ltx_video_installed"]:
            result = generate_video(
                title=title,
                category=category,
                output_path=output_path,
                **kwargs,
            )
            if result.get("success"):
                return result
            logger.warning(
                "GPU video generation failed, falling back to SVG: %s",
                result.get("error"),
            )

    # Tier 1 fallback: animated SVG
    svg_path = output_path
    if svg_path and svg_path.endswith(".mp4"):
        svg_path = svg_path.rsplit(".", 1)[0] + ".svg"
    return generate_svg_video(
        title=title,
        category=category,
        output_path=svg_path,
        width=kwargs.get("width", DEFAULT_WIDTH),
        height=kwargs.get("height", DEFAULT_HEIGHT),
    )


def create_post_video(title: str, topic: str = "") -> dict:
    """Generate a video for the Pulse pipeline.

    Alias matching the scheduler's expected API pattern (like create_post_image).
    Returns dict with 'path', 'method', 'duration_sec', 'success'.
    """
    result = generate_post_video(title=title, category=topic)
    result["url"] = result.get("path", "")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Pulse video generator (LTX-Video 2B / animated SVG fallback)")
    parser.add_argument("--prompt", type=str, help="Article title or custom prompt")
    parser.add_argument("--category", type=str, default="", help="Article category")
    parser.add_argument("--output", type=str, help="Output file path")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--frames", type=int, default=DEFAULT_NUM_FRAMES)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--svg-only", action="store_true", help="Force SVG (no GPU)")
    parser.add_argument("--gpu-only", action="store_true", help="Force GPU")
    parser.add_argument("--health", action="store_true", help="Check GPU health")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = check_gpu()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"CUDA available:       {result['cuda_available']}")
            print(f"Device:               {result['device_name'] or 'N/A'}")
            print(f"VRAM total:           {result['vram_total_gb']} GB")
            print(f"VRAM free:            {result['vram_free_gb']} GB")
            print(f"LTX-Video compatible: {result['ltx_video_compatible']}")
            print(f"LTX-Video installed:  {result['ltx_video_installed']}")
        return

    if not args.prompt:
        parser.error("--prompt is required (unless --health)")

    if args.svg_only:
        result = generate_svg_video(
            title=args.prompt,
            category=args.category,
            output_path=args.output,
            width=args.width,
            height=args.height,
        )
    elif args.gpu_only:
        result = generate_video(
            title=args.prompt,
            category=args.category,
            output_path=args.output,
            width=args.width,
            height=args.height,
            num_frames=args.frames,
            fps=args.fps,
            steps=args.steps,
            seed=args.seed,
        )
    else:
        result = generate_post_video(
            title=args.prompt,
            category=args.category,
            output_path=args.output,
            width=args.width,
            height=args.height,
            num_frames=args.frames,
            fps=args.fps,
            steps=args.steps,
            seed=args.seed,
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("success"):
            print(f"Generated:  {result['path']}")
            print(f"Method:     {result['method']}")
            print(f"Duration:   {result.get('duration_sec', 0)}s")
            print(f"Elapsed:    {result.get('elapsed_ms', 0)} ms")
            if result.get("prompt"):
                print(f"Prompt:     {result['prompt'][:100]}...")
        else:
            print(f"FAILED: {result.get('error', 'unknown')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
