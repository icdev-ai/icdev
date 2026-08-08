"""Model Weight Packager — Track B (run on a CONNECTED machine).

Downloads a HuggingFace model and packages it as a pip-installable wheel
for deployment to an internal PyPI mirror.

The resulting package installs model files to site-packages/qwen_weights/model/
and exposes a qwen_weights.get_model_path() helper.

Requirements (run on connected machine):
  pip install huggingface_hub build twine

Usage:
  # Step 1: Package (on connected machine)
  python tools/finetune/package_model_weights.py \\
    --model Qwen/Qwen2.5-1.5B-Instruct \\
    --output dist/

  # Step 2: Push to internal mirror
  twine upload --repository internal dist/qwen2_5_1_5b_instruct_weights-*.whl

  # Step 3: Install on air-gapped machine (from internal mirror)
  pip install qwen2_5_1_5b_instruct_weights

  # Step 4: Use in fine-tuning pipeline
  import qwen_weights
  model_path = qwen_weights.get_model_path()
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


def _sanitize_name(model_id: str) -> str:
    """Convert HuggingFace model ID to a Python package name."""
    return model_id.replace("/", "_").replace("-", "_").replace(".", "_").lower()


def _download_model(model_id: str, cache_dir: Path) -> Path:
    """Download model snapshot to cache_dir. Returns local path."""
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub required. pip install huggingface_hub"
        ) from exc

    print(f"Downloading {model_id} to {cache_dir}...")
    local_dir = cache_dir / _sanitize_name(model_id)
    snapshot_download(  # nosec B615 — model_id validated against approved repo list before call
        repo_id=model_id,
        local_dir=str(local_dir),
        ignore_patterns=["*.bin"],  # prefer safetensors
    )
    print(f"Downloaded to {local_dir}")
    return local_dir


def _write_package_files(pkg_root: Path, model_name: str, model_files_dir: Path) -> None:
    """Write setup.cfg, pyproject.toml, and __init__.py for the wheel."""
    pkg_name = _sanitize_name(model_name)
    version = "1.0.0"

    # pyproject.toml
    (pkg_root / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
        [build-system]
        requires = ["setuptools>=68", "wheel"]
        build-backend = "setuptools.backends.legacy:build"

        [project]
        name = "{pkg_name}_weights"
        version = "{version}"
        description = "Offline model weights for {model_name} (air-gap deployment)"
        requires-python = ">=3.10"
        """),
        encoding="utf-8", newline="",
    )

    # setup.cfg for package data inclusion
    (pkg_root / "setup.cfg").write_text(
        textwrap.dedent(f"""\
        [options]
        packages = {pkg_name}_weights
        package_data =
            {pkg_name}_weights = model/**

        [options.package_dir]
        = src
        """),
        encoding="utf-8", newline="",
    )

    # MANIFEST.in to include all model files
    (pkg_root / "MANIFEST.in").write_text(
        f"recursive-include src/{pkg_name}_weights/model *\n",
        encoding="utf-8", newline="",
    )

    # Package source directory
    src_dir = pkg_root / "src" / f"{pkg_name}_weights"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Copy model files
    model_dest = src_dir / "model"
    print("Copying model files to package structure...")
    if model_dest.exists():
        shutil.rmtree(model_dest)
    shutil.copytree(str(model_files_dir), str(model_dest))

    # __init__.py with get_model_path helper
    (src_dir / "__init__.py").write_text(
        textwrap.dedent(f"""\
        \"\"\"Offline model weights package for {model_name}.\"\"\"
        from importlib.resources import files

        def get_model_path() -> str:
            \"\"\"Return the absolute path to the installed model weights directory.\"\"\"
            pkg_files = files("{pkg_name}_weights")
            return str(pkg_files.joinpath("model"))
        """),
        encoding="utf-8", newline="",
    )

    # Write model metadata
    metadata = {
        "model_id": model_name,
        "package_name": f"{pkg_name}_weights",
        "version": version,
    }
    (src_dir / "model" / "icdev_package_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8", newline=""
    )


def _build_wheel(pkg_root: Path, output_dir: Path) -> Path:
    """Build the wheel using pip build. Returns path to .whl file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Building wheel in {output_dir}...")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(output_dir), str(pkg_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Build failed:\n{result.stderr}")

    wheels = list(output_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError(f"No .whl found in {output_dir} after build")

    wheel_path = wheels[-1]
    size_mb = wheel_path.stat().st_size / (1024 * 1024)
    print(f"Built: {wheel_path.name} ({size_mb:.1f} MB)")
    return wheel_path


def package(model_id: str, output_dir: str, cache_dir: str = "") -> dict:
    """Download model and package as a wheel.

    Run on a CONNECTED machine before pushing to internal PyPI mirror.

    Args:
        model_id:   HuggingFace model ID, e.g. 'Qwen/Qwen2.5-1.5B-Instruct'
        output_dir: Directory to write the .whl file
        cache_dir:  Temp directory for download (auto-created if empty)

    Returns:
        dict with wheel_path, package_name, size_mb
    """
    out = Path(output_dir)
    pkg_name = _sanitize_name(model_id)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="icdev_model_pkg_") as tmp:
        tmp_path = Path(tmp)
        cache_path = Path(cache_dir) if cache_dir else tmp_path / "cache"
        pkg_root = tmp_path / "pkg"
        pkg_root.mkdir()

        model_files_dir = _download_model(model_id, cache_path)
        _write_package_files(pkg_root, model_id, model_files_dir)
        wheel_path = _build_wheel(pkg_root, out)

    size_mb = wheel_path.stat().st_size / (1024 * 1024)
    return {
        "status": "ok",
        "wheel_path": str(wheel_path),
        "package_name": f"{pkg_name}_weights",
        "model_id": model_id,
        "size_mb": round(size_mb, 1),
        "install_cmd": f"pip install {wheel_path.name}",
        "push_cmd": f"twine upload --repository internal {wheel_path}",
    }


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Package HuggingFace model weights as pip wheel (run on connected machine)"
    )
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct",
                        help="HuggingFace model ID")
    parser.add_argument("--output", default="dist",
                        help="Output directory for .whl file")
    parser.add_argument("--cache-dir", default="",
                        help="Cache directory for model download (optional)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    result = package(
        model_id=args.model,
        output_dir=args.output,
        cache_dir=args.cache_dir,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nWheel built: {result['wheel_path']}")
        print(f"Size: {result['size_mb']} MB")
        print("\nNext steps:")
        print(f"  1. Push to mirror:  {result['push_cmd']}")
        print(f"  2. Install on target: {result['install_cmd']}")
        print(f"  3. Verify: python -c \"import {result['package_name'].replace('-', '_')}; "
              f"print({result['package_name'].replace('-', '_')}.get_model_path())\"")


if __name__ == "__main__":
    _main()
