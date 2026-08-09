# CUI // SP-CTI
"""tools/airgap/wheel_vendor \u2014 Admin-only PyPI wheel vendor for air-gap.

Parallel to ``tools/airgap/driver_vendor.py`` (Phase D5). Fetches pinned
PyPI wheels on a connected workstation, stashes them under
``vendor/wheels/{platform_tag}/``, and writes an ``SHA256SUM`` manifest
alongside each bucket.

Runtime refuses to execute when ``is_airgap()`` is true \u2014 air-gap hosts
install from the vendored bucket via::

    pip install --no-index --find-links=vendor/wheels/{tag}/ \\
        -r vendor/wheels/requirements-<topic>.txt

Usage::

    python tools/airgap/wheel_vendor.py --fetch --topic sharepoint
    python tools/airgap/wheel_vendor.py --fetch --topic sharepoint --platform py314-manylinux_2_28_x86_64
    python tools/airgap/wheel_vendor.py --verify --topic sharepoint
    python tools/airgap/wheel_vendor.py --list
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import logging
import os
import platform as _platform
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = get_logger("icdev.airgap.wheel_vendor")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WHEELS_DIR = BASE_DIR / "vendor" / "wheels"


# ---------------------------------------------------------------------------
# Platform tag + topic resolution
# ---------------------------------------------------------------------------


def current_platform_tag() -> str:
    """Return a directory name like ``py314-win_amd64``.

    Format: ``py{major}{minor}-{sys.platform}_{machine}`` normalized to
    lowercase. ``win32`` is remapped to ``win_amd64`` on 64-bit Windows
    because that's the canonical pip wheel tag for Windows x64.
    """
    major = sys.version_info.major
    minor = sys.version_info.minor
    sysplat = sys.platform
    machine = _platform.machine().lower() or "unknown"
    if sysplat == "win32":
        # pip convention; win_amd64 or win32 depending on bitness
        sysplat = "win_amd64" if machine in ("amd64", "x86_64") else "win32"
        machine_suffix = ""
    else:
        machine_suffix = f"_{machine}"
    return f"py{major}{minor}-{sysplat}{machine_suffix}"


def _requirements_path(topic: str) -> Path:
    path = WHEELS_DIR / f"requirements-{topic}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"No pin file for topic={topic!r}: expected {path} \u2014 "
            f"create it with version-pinned entries before --fetch"
        )
    return path


def _bucket_dir(tag: str) -> Path:
    return WHEELS_DIR / tag


# ---------------------------------------------------------------------------
# Safety: refuse to fetch in air-gap
# ---------------------------------------------------------------------------


def _assert_connected() -> None:
    """Hard-fail when invoked in an air-gap environment.

    Uses tools.airgap.detector.is_airgap if available; falls back to an
    env-var check (``ICDEV_AIRGAP=true``). The point is to prevent
    accidental runs on air-gap hosts where the fetch would silently fail.
    """
    try:
        from tools.airgap.detector import is_airgap  # noqa: PLC0415
        airgap = is_airgap()
    except Exception:
        airgap = os.environ.get("ICDEV_AIRGAP", "").lower() in ("1", "true", "yes")
    if airgap:
        raise RuntimeError(
            "wheel_vendor.py refuses to run in air-gap mode. "
            "Fetch wheels on a connected admin workstation, then transport "
            "vendor/wheels/ to the air-gap host."
        )


# ---------------------------------------------------------------------------
# SHA256 helpers (same format as vendor/drivers/)
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_checksum_manifest(bucket: Path) -> dict[str, str]:
    """Hash every ``*.whl`` in bucket, write SHA256SUM, return the mapping."""
    digests: dict[str, str] = {}
    for whl in sorted(bucket.glob("*.whl")):
        digests[whl.name] = _sha256_file(whl)
    manifest = bucket / "SHA256SUM"
    lines = [f"{d}  {name}\n" for name, d in digests.items()]
    manifest.write_text("".join(lines), encoding="utf-8", newline="")
    return digests


def _read_checksum_manifest(bucket: Path) -> dict[str, str]:
    manifest = bucket / "SHA256SUM"
    if not manifest.exists():
        return {}
    result: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            result[parts[1].strip()] = parts[0].strip()
    return result


# ---------------------------------------------------------------------------
# Fetch / verify / list
# ---------------------------------------------------------------------------


def fetch(topic: str, platform_tag: str | None = None) -> dict[str, Any]:
    """Download wheels matching the topic's requirements file into a bucket.

    Uses ``pip download`` with ``--only-binary=:all:`` so the bucket
    contains wheels only (no sdists that would need build tools at
    install-time).
    """
    _assert_connected()
    tag = platform_tag or current_platform_tag()
    req = _requirements_path(topic)
    bucket = _bucket_dir(tag)
    bucket.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "pip", "download",
        "--dest", str(bucket),
        "--only-binary=:all:",
        "--requirement", str(req),
    ]
    # Cross-platform fetches need --platform / --python-version hints
    if platform_tag and platform_tag != current_platform_tag():
        # Parse "py314-win_amd64" → py_version=3.14, platform=win_amd64
        pytag, rest = tag.split("-", 1)
        pyver_major = pytag[2]
        pyver_minor = pytag[3:]
        cmd += [
            "--python-version", f"{pyver_major}.{pyver_minor}",
            "--platform", rest,
            # --implementation default cp is fine
        ]

    logger.info("pip download: %s", " ".join(cmd))
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pip download failed (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    # Hash + manifest
    digests = _write_checksum_manifest(bucket)
    return {
        "topic": topic,
        "platform_tag": tag,
        "bucket": str(bucket),
        "wheels": sorted(digests.keys()),
        "sha256sum_entries": len(digests),
    }


def verify(topic: str, platform_tag: str | None = None) -> dict[str, Any]:
    """Recompute every wheel's SHA and compare to the stored manifest.

    Returns a dict with ``ok``, ``verified``, ``mismatched``, ``missing``.
    """
    tag = platform_tag or current_platform_tag()
    bucket = _bucket_dir(tag)
    if not bucket.exists():
        return {
            "ok": False,
            "topic": topic,
            "platform_tag": tag,
            "error": f"bucket missing: {bucket}",
        }
    stored = _read_checksum_manifest(bucket)
    if not stored:
        return {
            "ok": False,
            "topic": topic,
            "platform_tag": tag,
            "error": "SHA256SUM missing or empty",
        }

    verified: list[str] = []
    mismatched: list[dict[str, str]] = []
    missing: list[str] = []

    disk_wheels = {p.name for p in bucket.glob("*.whl")}
    for name, expected in stored.items():
        path = bucket / name
        if not path.exists():
            missing.append(name)
            continue
        actual = _sha256_file(path)
        if actual == expected:
            verified.append(name)
        else:
            mismatched.append({"name": name, "expected": expected, "actual": actual})
    extra = sorted(disk_wheels - set(stored.keys()))

    ok = not (mismatched or missing)
    return {
        "ok": ok,
        "topic": topic,
        "platform_tag": tag,
        "bucket": str(bucket),
        "verified": verified,
        "mismatched": mismatched,
        "missing": missing,
        "extra_on_disk_no_hash": extra,
    }


def list_buckets() -> dict[str, Any]:
    """List every platform bucket under vendor/wheels/ and its contents."""
    buckets: list[dict[str, Any]] = []
    if not WHEELS_DIR.exists():
        return {"wheels_dir": str(WHEELS_DIR), "buckets": []}
    for sub in sorted(WHEELS_DIR.iterdir()):
        if sub.is_dir() and not sub.name.startswith("."):
            wheels = sorted(w.name for w in sub.glob("*.whl"))
            manifest = (sub / "SHA256SUM").exists()
            buckets.append({
                "platform_tag": sub.name,
                "wheels": wheels,
                "wheel_count": len(wheels),
                "has_manifest": manifest,
            })
    topics = sorted(
        p.stem.replace("requirements-", "")
        for p in WHEELS_DIR.glob("requirements-*.txt")
    )
    return {
        "wheels_dir": str(WHEELS_DIR),
        "topics": topics,
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="ICDEV\u2122 air-gap PyPI wheel vendor (admin-only).",
    )
    parser.add_argument("--fetch", action="store_true", help="Download wheels for --topic")
    parser.add_argument("--verify", action="store_true", help="Verify SHA256 for --topic")
    parser.add_argument("--list", action="store_true", help="List vendored buckets + topics")
    parser.add_argument("--topic", type=str, help="Requirements topic (matches vendor/wheels/requirements-<topic>.txt)")
    parser.add_argument(
        "--platform", type=str, default=None,
        help="Override platform tag (default: current host). Example: py314-manylinux_2_28_x86_64",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.list:
            out = list_buckets()
        elif args.fetch:
            if not args.topic:
                parser.error("--fetch requires --topic")
            out = fetch(args.topic, platform_tag=args.platform)
        elif args.verify:
            if not args.topic:
                parser.error("--verify requires --topic")
            out = verify(args.topic, platform_tag=args.platform)
        else:
            parser.print_help()
            return 2
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps(out, indent=2))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(_main())
