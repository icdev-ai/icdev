#!/usr/bin/env python3
# CUI // SP-CTI
"""vLLM air-gap bundle: the pinned image + a model-weights tarball (omx-vllm-05).

    low side (connected)                          high side (air-gapped)
    --------------------                          ----------------------
    save --model <repo>   -> vendor/images/vllm/*.tar   -> load --bundle ...
                             vendor/models/vllm/*.tar
                             (transport media)

THE IMAGE HALF IS NOT RE-IMPLEMENTED. It is ``tools/airgap/image_vendor.py
--topic vllm`` -- the same mechanism, pin file convention and three-status
verdict the floci image cache uses. The pin is
``vendor/images/images-vllm.txt``.

THE WEIGHTS HALF FOLLOWS THE SAME RULES:

* NOTHING DOWNLOADS. ``save`` tars what the local Hugging Face cache already
  holds. A model absent from the cache is REPORTED and the run fails; fetch it
  on the connected side first (``HF_HOME=data/vllm/hf-cache hf download <repo>``).
* THE TAR IS HASHED. ``MANIFEST.json`` records each tar's sha256 and the
  snapshot revisions it carries; ``verify`` re-hashes and ``load`` refuses to
  extract anything that does not match.
* EXTRACTION IS CONFINED. A member that would land outside the target cache
  (absolute path, ``..``, a link pointing out) is refused, not skipped.

AFTER ``docker load`` THE IMAGE RESOLVES ONLY BY ITS ID: it carries no tag and
no repo digest, so the compose file's ``repo:tag@sha256:...`` default cannot
find it. ``load`` therefore tags the verified image locally and prints the
``VLLM_IMAGE=`` line to put in ``.env``.

Usage::

    python deploy/offline/vllm_bundle.py save   --model Qwen/Qwen2.5-1.5B-Instruct-AWQ
    python deploy/offline/vllm_bundle.py verify
    python deploy/offline/vllm_bundle.py load
    # add --json for machine-readable output, --skip-image for weights only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HF_CACHE = REPO_ROOT / "data" / "vllm" / "hf-cache"
DEFAULT_MODELS_BUNDLE = REPO_ROOT / "vendor" / "models" / "vllm"
IMAGE_VENDOR = REPO_ROOT / "tools" / "airgap" / "image_vendor.py"
IMAGE_TOPIC = "vllm"
LOCAL_TAG_REPO = "icdev-vendored/vllm-openai"
MANIFEST_NAME = "MANIFEST.json"

STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_UNMEASURED = "unmeasured"

_LOADED_ID_RE = re.compile(r"Loaded image ID:\s*(sha256:[0-9a-f]{64})")


def hf_repo_dirname(model: str) -> str:
    """``Qwen/Qwen2.5-1.5B`` -> ``models--Qwen--Qwen2.5-1.5B`` (the HF hub layout)."""
    model = model.strip().strip("/")
    if not model or ".." in model or "\\" in model:
        raise ValueError(f"not a Hugging Face repo id: {model!r}")
    return "models--" + model.replace("/", "--")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(bundle: Path) -> dict[str, Any] | None:
    path = bundle / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# image half -- delegated to tools/airgap/image_vendor.py
# ---------------------------------------------------------------------------


def _image_vendor(action: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(IMAGE_VENDOR), f"--{action}", "--topic", IMAGE_TOPIC, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
    )
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {"status": STATUS_UNMEASURED, "ok": False,
               "note": (proc.stderr or proc.stdout).strip()[:300]}
    out["exit_code"] = proc.returncode
    return out


def _docker(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _resolve_loaded_image(pin_ref: str, digest: str, load_output: str) -> tuple[str | None, str]:
    """Find the loaded image's ID. A ladder, and the rung that answered is reported."""
    for rung, ref in (("pin_ref", pin_ref), ("digest_as_id", digest)):
        proc = _docker(["image", "inspect", ref, "--format", "{{.Id}}"])
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip(), rung
    match = _LOADED_ID_RE.search(load_output or "")
    if match:
        return match.group(1), "load_output"
    return None, "unresolved"


def _tag_loaded_image(image_result: dict[str, Any]) -> dict[str, Any]:
    tagged: dict[str, Any] = {"status": STATUS_UNMEASURED}
    for img in image_result.get("loaded") or []:
        ref, digest = img.get("ref", ""), img.get("digest", "")
        image_id, rung = _resolve_loaded_image(ref, digest, img.get("load_output", ""))
        if not image_id:
            tagged["note"] = ("loaded image could not be resolved by pin, digest or load "
                              "output; find it with `docker image ls -a` and tag it by hand")
            continue
        local = f"{LOCAL_TAG_REPO}:{digest.split(':')[-1][:12]}"
        proc = _docker(["tag", image_id, local])
        if proc.returncode != 0:
            tagged = {"status": STATUS_FAILED, "error": (proc.stderr or proc.stdout).strip()[:300]}
            continue
        tagged = {"status": STATUS_VERIFIED, "image_id": image_id, "resolved_by": rung,
                  "local_tag": local, "env_line": f"VLLM_IMAGE={local}"}
    return tagged


# ---------------------------------------------------------------------------
# weights half
# ---------------------------------------------------------------------------


def save_weights(model: str, hf_cache: Path, bundle: Path) -> dict[str, Any]:
    dirname = hf_repo_dirname(model)
    src = hf_cache / "hub" / dirname
    out: dict[str, Any] = {"model": model, "source": str(src), "bundle": str(bundle)}
    snapshots = src / "snapshots"
    revisions = sorted(p.name for p in snapshots.iterdir()) if snapshots.is_dir() else []
    if not revisions:
        out.update(status=STATUS_FAILED, ok=False, reason="absent_from_local_cache",
                   note=(f"no snapshot under {src}. Nothing is downloaded here: fetch it on "
                         f"the connected side first, e.g. HF_HOME={hf_cache} hf download {model}"))
        return out

    bundle.mkdir(parents=True, exist_ok=True)
    tar_path = bundle / f"{dirname}.tar"
    with tarfile.open(tar_path, "w") as tar:
        tar.add(str(src), arcname=f"hub/{dirname}")
    entry = {"model": model, "revisions": revisions, "tar_name": tar_path.name,
             "tar_sha256": _sha256_file(tar_path), "bytes": tar_path.stat().st_size,
             "created_at": datetime.now(timezone.utc).isoformat()}

    manifest = _read_manifest(bundle) or {"topic": "vllm-weights", "models": []}
    manifest["models"] = [m for m in manifest.get("models", []) if m.get("model") != model]
    manifest["models"].append(entry)
    (bundle / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (bundle / "SHA256SUM").write_text(
        "".join(f"{m['tar_sha256']}  {m['tar_name']}\n" for m in manifest["models"]),
        encoding="utf-8")
    out.update(status=STATUS_VERIFIED, ok=True, saved=entry)
    return out


def verify_weights(bundle: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"bundle": str(bundle), "checked": []}
    manifest = _read_manifest(bundle)
    if not manifest or not manifest.get("models"):
        out.update(status=STATUS_UNMEASURED, ok=False,
                   note=f"{MANIFEST_NAME} missing or empty under {bundle} -- nothing to verify")
        return out
    failed = False
    for entry in manifest["models"]:
        tar_path = bundle / entry["tar_name"]
        actual = _sha256_file(tar_path) if tar_path.is_file() else None
        ok = actual == entry["tar_sha256"]
        failed = failed or not ok
        out["checked"].append({"tar_name": entry["tar_name"], "ok": ok,
                               "expected": entry["tar_sha256"], "actual": actual})
    out["status"] = STATUS_FAILED if failed else STATUS_VERIFIED
    out["ok"] = not failed
    return out


def _confined_members(tar: tarfile.TarFile, dest: Path) -> list[tarfile.TarInfo]:
    """Every member, or ValueError if ANY would land outside ``dest``."""
    root = dest.resolve()
    members = tar.getmembers()
    for m in members:
        name = PurePosixPath(m.name)
        if name.is_absolute() or ".." in name.parts or not m.name.startswith("hub/"):
            raise ValueError(f"refusing tar member outside the cache: {m.name!r}")
        if m.issym() or m.islnk():
            base = (root / m.name).parent if m.issym() else root
            target = (base / m.linkname).resolve()
            if os.path.isabs(m.linkname) or not target.is_relative_to(root):
                raise ValueError(f"refusing link leaving the cache: {m.name!r} -> {m.linkname!r}")
        elif not (m.isfile() or m.isdir()):
            raise ValueError(f"refusing special tar member: {m.name!r}")
    return members


def load_weights(bundle: Path, hf_cache: Path) -> dict[str, Any]:
    out = verify_weights(bundle)
    if not out["ok"]:
        out["note"] = "bundle did not verify -- nothing was extracted"
        return out
    hf_cache.mkdir(parents=True, exist_ok=True)
    extracted = []
    for entry in _read_manifest(bundle)["models"]:
        with tarfile.open(bundle / entry["tar_name"], "r") as tar:
            members = _confined_members(tar, hf_cache)
            tar.extractall(str(hf_cache), members=members)  # noqa: S202 -- confined above
        extracted.append({"model": entry["model"], "revisions": entry["revisions"]})
    out["extracted"] = extracted
    out["hf_cache"] = str(hf_cache)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    for half in ("weights", "image", "image_tag"):
        part = result.get(half)
        if part:
            print(f"{half}: {part.get('status')}" + (f" -- {part['note']}" if part.get("note") else ""))
    for line in result.get("env_lines", []):
        print(f"  add to .env:  {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("action", choices=["save", "verify", "load"])
    parser.add_argument("--model", help="Hugging Face repo id (save)")
    parser.add_argument("--hf-cache", type=Path, default=DEFAULT_HF_CACHE,
                        help="HF_HOME the vllm service mounts (default: data/vllm/hf-cache)")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_MODELS_BUNDLE,
                        help="weights bundle directory (default: vendor/models/vllm)")
    parser.add_argument("--skip-image", action="store_true", help="weights only")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result: dict[str, Any] = {"action": args.action}
    if args.action == "save":
        if not args.model:
            parser.error("save requires --model")
        result["weights"] = save_weights(args.model, args.hf_cache, args.bundle)
        if not args.skip_image:
            result["image"] = _image_vendor("save")
    elif args.action == "verify":
        result["weights"] = verify_weights(args.bundle)
        if not args.skip_image:
            result["image"] = _image_vendor("verify")
    else:
        result["weights"] = load_weights(args.bundle, args.hf_cache)
        result["env_lines"] = ["VLLM_HF_OFFLINE=1"]
        if not args.skip_image and result["weights"].get("ok"):
            image = _image_vendor("load")
            result["image"] = image
            if image.get("ok"):
                result["image_tag"] = _tag_loaded_image(image)
                if result["image_tag"].get("env_line"):
                    result["env_lines"].append(result["image_tag"]["env_line"])

    _print(result, args.json)
    halves = [result[k] for k in ("weights", "image", "image_tag") if k in result]
    return 0 if all(h.get("status") == STATUS_VERIFIED for h in halves) else 1


if __name__ == "__main__":
    sys.exit(main())
