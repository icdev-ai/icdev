"""Draft model setup helper — download, convert, and register DeepSpec draft models with Ollama.

Supports two workflows:
  1. Easy path  — pull a small base model from Ollama registry (no conversion needed)
  2. Full path  — download DeepSpec checkpoint from HuggingFace, convert to GGUF, register

Usage:
    python -m icdev.tools.llm.draft_model_setup --easy --target qwen3:4b
    python -m icdev.tools.llm.draft_model_setup --full --checkpoint deepseek-ai/dspark_qwen3_4b_block7
    python -m icdev.tools.llm.draft_model_setup --status
    python -m icdev.tools.llm.draft_model_setup --easy --target qwen3:4b --json
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# DeepSpec checkpoint → compatible Ollama target model
_DEEPSPEC_CHECKPOINTS: dict[str, str] = {
    "deepseek-ai/dspark_qwen3_4b_block7":  "qwen3:4b",
    "deepseek-ai/dspark_qwen3_8b_block7":  "qwen3:8b",
    "deepseek-ai/dspark_qwen3_14b_block7": "qwen3:14b",
    "deepseek-ai/eagle3_qwen3_4b_ttt7":    "qwen3:4b",
    "deepseek-ai/eagle3_qwen3_8b_ttt7":    "qwen3:8b",
    "deepseek-ai/eagle3_qwen3_14b_ttt7":   "qwen3:14b",
    "deepseek-ai/dspark_gemma4_12b_block7": "gemma3:12b",
    "deepseek-ai/eagle3_gemma4_12b_ttt7":  "gemma3:12b",
}

# Small base-model drafts already on Ollama registry (no conversion needed)
_EASY_DRAFTS: dict[str, str] = {
    "qwen3:4b":   "qwen3:0.6b",
    "qwen3:8b":   "qwen3:1.7b",
    "qwen3:14b":  "qwen3:1.7b",
    "qwen3:32b":  "qwen3:1.7b",
    "llama3.2:latest": "llama3.2:1b",
    "llama3.1:8b": "llama3.2:1b",
    "gemma3:12b": "gemma3:1b",
}


@dataclass
class SetupResult:
    success: bool
    draft_model: str = ""
    target_model: str = ""
    method: str = ""        # "easy" | "full" | "none"
    steps: list[str] = field(default_factory=list)
    error: str = ""


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd,
        timeout=600,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _ollama_pull(model: str) -> tuple[bool, str]:
    """Pull a model via ollama CLI. Returns (success, message)."""
    rc, out, err = _run(["ollama", "pull", model])
    if rc == 0:
        return True, f"Pulled {model}"
    return False, err or out


def _ollama_create(name: str, modelfile_path: Path) -> tuple[bool, str]:
    """Register a local GGUF with Ollama via a Modelfile. Windows-safe."""
    rc, out, err = _run(["ollama", "create", name, "-f", str(modelfile_path)])
    if rc == 0:
        return True, f"Registered {name}"
    return False, err or out


def _ollama_list() -> list[str]:
    """Return list of model names currently in Ollama."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"{_OLLAMA_BASE_URL}/api/tags", timeout=3) as r:
            body = json.loads(r.read())
            return [m.get("name", "") for m in body.get("models", [])]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Easy path
# ---------------------------------------------------------------------------

def setup_easy(target_model: str) -> SetupResult:
    """Pull a small base-model draft from Ollama registry.

    No conversion needed. Not as fast as a DeepSpec checkpoint, but works
    out of the box on any machine with Ollama installed.
    """
    draft = _EASY_DRAFTS.get(target_model)
    if not draft:
        # Fall back: pick smallest qwen3 if target is a qwen3 variant
        base = target_model.split(":")[0].lower()
        for key, val in _EASY_DRAFTS.items():
            if key.startswith(base):
                draft = val
                break
    if not draft:
        return SetupResult(
            success=False,
            error=f"No easy-path draft known for '{target_model}'. "
                  f"Known targets: {', '.join(_EASY_DRAFTS)}",
        )

    steps: list[str] = []

    # Pull draft
    ok, msg = _ollama_pull(draft)
    steps.append(f"ollama pull {draft}: {'OK' if ok else 'FAILED — ' + msg}")
    if not ok:
        return SetupResult(success=False, draft_model=draft, steps=steps, error=msg)

    # Pull target if not already present
    loaded = _ollama_list()
    if not any(target_model in m for m in loaded):
        ok2, msg2 = _ollama_pull(target_model)
        steps.append(f"ollama pull {target_model}: {'OK' if ok2 else 'FAILED — ' + msg2}")
        if not ok2:
            return SetupResult(success=False, steps=steps, error=msg2)
    else:
        steps.append(f"{target_model} already loaded — skipped pull")

    return SetupResult(
        success=True,
        draft_model=draft,
        target_model=target_model,
        method="easy",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Full path (HuggingFace → GGUF → Ollama)
# ---------------------------------------------------------------------------

def setup_full(checkpoint: str, output_dir: Path | None = None) -> SetupResult:
    """Download DeepSpec checkpoint from HuggingFace, convert to GGUF, register with Ollama.

    IMPORTANT LIMITATIONS:
      - DSpark, Eagle3, and DFlash use CUSTOM architectures (Qwen3DSparkModel etc.)
        that llama.cpp does NOT support. GGUF conversion will fail with
        "Model Qwen3DSparkModel is not supported" for all DeepSpec checkpoints.
      - True DeepSpec speculative decoding requires SGLang on Linux/Mac with CUDA.
      - On Windows: use --easy instead (qwen3:0.6b as draft, no conversion needed).

    Requires:
      - huggingface_hub Python package  (pip install huggingface_hub)
      - llama.cpp with convert_hf_to_gguf.py  (https://github.com/ggerganov/llama.cpp)
        pointed to by LLAMA_CPP_DIR env var, or auto-detected in common locations
      - PyTorch <= 3.13 (torch 2.6 has no Python 3.14 wheels)
    """
    steps: list[str] = []
    out_dir = output_dir or Path.home() / ".cache" / "icdev" / "draft_models"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: download from HuggingFace
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import]
    except ImportError:
        return SetupResult(
            success=False, steps=steps,
            error="huggingface_hub not installed. Run: pip install huggingface_hub",
        )

    slug = checkpoint.replace("/", "_")
    local_dir = out_dir / slug
    steps.append(f"Downloading {checkpoint} → {local_dir}")
    try:
        snapshot_download(repo_id=checkpoint, local_dir=str(local_dir))
        steps[-1] += " OK"
    except Exception as exc:
        steps[-1] += f" FAILED: {exc}"
        return SetupResult(success=False, steps=steps, error=str(exc))

    # Step 2: find llama.cpp convert script
    llama_cpp_dir = os.environ.get("LLAMA_CPP_DIR", "")
    convert_script: Path | None = None
    search_paths = [Path(llama_cpp_dir)] if llama_cpp_dir else []
    search_paths += [
        Path.home() / "llama.cpp",
        Path("C:/llama.cpp"),
        Path("C:/tools/llama.cpp"),
        Path("/opt/llama.cpp"),
        Path("/usr/local/llama.cpp"),
    ]
    for p in search_paths:
        candidate = p / "convert_hf_to_gguf.py"
        if candidate.exists():
            convert_script = candidate
            break

    if not convert_script:
        return SetupResult(
            success=False, steps=steps,
            error=(
                "llama.cpp convert_hf_to_gguf.py not found. "
                "Clone https://github.com/ggerganov/llama.cpp and set "
                "LLAMA_CPP_DIR=<path> env var, then retry."
            ),
        )

    # Step 3: convert to GGUF
    gguf_path = out_dir / f"{slug}.gguf"
    steps.append(f"Converting to GGUF → {gguf_path}")
    rc, out, err = _run(
        [sys.executable, str(convert_script), str(local_dir), "--outfile", str(gguf_path)],
    )
    if rc != 0:
        steps[-1] += f" FAILED: {err or out}"
        return SetupResult(success=False, steps=steps, error=err or out)
    steps[-1] += " OK"

    # Step 4: write Modelfile (Windows-safe: write to file, not heredoc)
    modelfile = out_dir / f"{slug}.Modelfile"
    modelfile.write_text(f"FROM {gguf_path}\n", encoding="utf-8")
    steps.append(f"Wrote Modelfile → {modelfile}")

    # Step 5: register with Ollama
    ollama_name = slug.lower().replace("-", "_")
    steps.append(f"Registering as {ollama_name} in Ollama")
    ok, msg = _ollama_create(ollama_name, modelfile)
    steps[-1] += f": {'OK' if ok else 'FAILED — ' + msg}"
    if not ok:
        return SetupResult(success=False, steps=steps, error=msg)

    target = _DEEPSPEC_CHECKPOINTS.get(checkpoint, "")
    return SetupResult(
        success=True,
        draft_model=ollama_name,
        target_model=target,
        method="full",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------

def status() -> dict:
    """Report which draft models are loaded and whether spec-dec is ready."""
    from icdev.tools.llm.speculative_decoder import _discover_draft_model, _DRAFT_MODEL_PATTERNS, _SMALL_MODEL_SIZES
    loaded = _ollama_list()
    draft = _discover_draft_model(_OLLAMA_BASE_URL)
    return {
        "ollama_reachable": bool(loaded or _ollama_list() is not None),
        "loaded_models": loaded,
        "detected_draft": draft,
        "spec_dec_ready": bool(draft),
        "easy_path_available": any(
            any(m.lower().endswith(sz) for sz in _SMALL_MODEL_SIZES) for m in loaded
        ),
        "deepspec_available": any(
            any(pat in m.lower() for pat in ("dspark", "eagle3", "dflash")) for m in loaded
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Draft model setup for ICDEV speculative decoding")
    parser.add_argument("--easy", action="store_true", help="Pull small base-model draft from Ollama")
    parser.add_argument("--full", action="store_true", help="Download + convert DeepSpec checkpoint")
    parser.add_argument("--target", default="qwen3:4b", help="Target model (for --easy)")
    parser.add_argument("--checkpoint", default="deepseek-ai/dspark_qwen3_4b_block7",
                        help="HuggingFace checkpoint (for --full)")
    parser.add_argument("--output-dir", help="Output directory for GGUF files (--full)")
    parser.add_argument("--status", action="store_true", help="Check current spec-dec status")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    def _print(data):
        if args.json:
            print(json.dumps(data, indent=2))
        elif isinstance(data, dict):
            for k, v in data.items():
                print(f"  {k}: {v}")
        else:
            print(data)

    if args.status:
        _print(status())
        sys.exit(0)

    if args.easy:
        result = setup_easy(args.target)
        if args.json:
            _print(vars(result))
        else:
            print(f"{'OK' if result.success else 'FAILED'}: {result.draft_model or result.error}")
            for s in result.steps:
                print(f"  {s}")
            if result.success:
                print(f"\nSpec-dec ready: draft={result.draft_model} target={result.target_model}")
        sys.exit(0 if result.success else 1)

    if args.full:
        out = Path(args.output_dir) if args.output_dir else None
        result = setup_full(args.checkpoint, output_dir=out)
        if args.json:
            _print(vars(result))
        else:
            print(f"{'OK' if result.success else 'FAILED'}")
            for s in result.steps:
                print(f"  {s}")
            if result.success:
                print(f"\nSpec-dec ready: draft={result.draft_model}")
                if result.target_model:
                    print(f"  Run: ollama pull {result.target_model}")
        sys.exit(0 if result.success else 1)

    parser.print_help()
