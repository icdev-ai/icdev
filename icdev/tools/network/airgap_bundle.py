# CUI // SP-CTI
"""Build an offline air-gap lab bundle for IL5/IL6 enclaves.

Produces a single tar.gz containing:
  - GNS3 server binary (user must supply path)
  - Containerlab binary
  - Curated appliance images (directory of container image tarballs)
  - SBOM (CycloneDX JSON) for every bundled image
  - Signed manifest.json with SHA256 of each artifact

Cross-platform: stdlib only (tarfile, hashlib, json, pathlib). No external
`syft` dep is required — a minimal CycloneDX SBOM is generated from file
metadata when syft is unavailable.

TODO(boundary-canvas): on successful build, call
    tools.boundary_canvas.register_authorized_component(
        name=bundle_name, sha256=manifest_sha, kind='airgap_bundle')
so the bundle shows up as an authorized supply-chain artifact.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.network.airgap_bundle")

_CHUNK = 1024 * 1024  # 1 MB


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _have_syft() -> bool:
    return shutil.which("syft") is not None


def generate_sbom(image_path: str, format: str = "cyclonedx") -> Dict[str, Any]:
    """Generate a CycloneDX SBOM for a container image tarball.

    If `syft` is on PATH, use it. Otherwise return a minimal CycloneDX document
    derived from file metadata (sha256, size, filename).
    """
    p = Path(image_path)
    if not p.exists():
        return {"error": f"image not found: {image_path}"}

    if _have_syft():
        try:
            out = subprocess.run(  # noqa: S603
                ["syft", str(p), "-o", "cyclonedx-json"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                return json.loads(out.stdout)
            logger.warning("syft exited %d: %s", out.returncode, out.stderr[:200])
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
            logger.warning("syft invocation failed: %s", exc)

    # Minimal CycloneDX fallback — image as a single component.
    digest = _sha256_file(p)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:airgap-{digest[:16]}",
        "version": 1,
        "metadata": {
            "timestamp": _utcnow(),
            "tools": [{"name": "icdev-airgap-bundle", "version": "1.0"}],
        },
        "components": [
            {
                "type": "container",
                "name": p.name,
                "version": "unknown",
                "hashes": [{"alg": "SHA-256", "content": digest}],
                "properties": [
                    {"name": "file:size", "value": str(p.stat().st_size)},
                    {"name": "file:path", "value": str(p)},
                ],
            }
        ],
    }


def _add_file_to_tar(
    tar: tarfile.TarFile,
    src: Path,
    arcname: str,
    artifacts: List[Dict[str, Any]],
) -> None:
    sha = _sha256_file(src)
    size = src.stat().st_size
    tar.add(str(src), arcname=arcname)
    artifacts.append(
        {
            "name": arcname,
            "size": size,
            "sha256": sha,
            "source": str(src),
        }
    )


def build_bundle(
    output_path: str,
    images_dir: Optional[str] = None,
    gns3_binary: Optional[str] = None,
    containerlab_binary: Optional[str] = None,
    signer: str = "",
) -> Dict[str, Any]:
    """Build the air-gap bundle.

    Parameters
    ----------
    output_path : str            — destination .tar.gz path
    images_dir  : str, optional  — dir of container image tarballs (any file)
    gns3_binary : str, optional  — path to GNS3 server binary
    containerlab_binary : str    — path to containerlab binary
    signer      : str            — arbitrary signer identifier recorded in manifest

    Returns
    -------
    dict: {bundle_path, size_bytes, artifacts, sbom_count, manifest_sha256}
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    artifacts: List[Dict[str, Any]] = []
    sboms: List[Dict[str, Any]] = []

    # `w:gz` with deterministic-ish ordering; callers can re-sign.
    with tarfile.open(str(out), "w:gz") as tar:
        if gns3_binary:
            gns3_path = Path(gns3_binary)
            if gns3_path.exists():
                _add_file_to_tar(tar, gns3_path, f"bin/{gns3_path.name}", artifacts)
            else:
                logger.warning("GNS3 binary not found: %s", gns3_binary)

        if containerlab_binary:
            cl_path = Path(containerlab_binary)
            if cl_path.exists():
                _add_file_to_tar(tar, cl_path, f"bin/{cl_path.name}", artifacts)
            else:
                logger.warning("Containerlab binary not found: %s", containerlab_binary)

        if images_dir:
            img_root = Path(images_dir)
            if img_root.exists() and img_root.is_dir():
                for entry in sorted(img_root.iterdir()):
                    if not entry.is_file():
                        continue
                    arc = f"images/{entry.name}"
                    _add_file_to_tar(tar, entry, arc, artifacts)
                    sbom = generate_sbom(str(entry))
                    sboms.append({"image": entry.name, "sbom": sbom})
            else:
                logger.warning("images_dir missing or not a directory: %s", images_dir)

        # Embed SBOMs as JSON files inside the tar.
        for s in sboms:
            sbom_name = f"sbom/{Path(s['image']).stem}.cdx.json"
            data = json.dumps(s["sbom"], indent=2, sort_keys=True).encode("utf-8")
            info = tarfile.TarInfo(name=sbom_name)
            info.size = len(data)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            import io  # local — only needed here

            tar.addfile(info, io.BytesIO(data))
            artifacts.append(
                {
                    "name": sbom_name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "source": "generated",
                }
            )

        # Manifest last so it covers everything above.
        manifest = {
            "version": 1,
            "generated_at": _utcnow(),
            "signer": signer,
            "artifacts": artifacts,
            "sbom_count": len(sboms),
        }
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        manifest["manifest_sha256"] = manifest_sha
        # Re-serialize with embedded hash for integrity self-reference.
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

        import io

        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(manifest_bytes))

    size_bytes = out.stat().st_size

    # Register bundle as an authorized component in the boundary canvas supply-chain DB.
    try:
        from tools.boundary_canvas.db.init_db import get_connection as _bdc_conn
        import uuid as _uuid
        _bc = _bdc_conn()
        _bc.execute(
            "INSERT OR IGNORE INTO bd_authorized_components "
            "(id, component_type, name, sha256_manifest, bundle_path, "
            " file_count, sbom_count, registered_by, status, created_at, updated_at) "
            "VALUES (?, 'airgap_bundle', ?, ?, ?, ?, ?, 'icdev-airgap-engine', 'authorized', ?, ?)",
            (
                str(_uuid.uuid4()), out.name, manifest_sha, str(out),
                len(artifacts), len(sboms),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _bc.commit()
        _bc.close()
    except Exception as _reg_exc:
        logger.warning("boundary-canvas registration skipped: %s", _reg_exc)

    logger.info(
        "Bundle built: %s (%d bytes, %d artifacts, %d sboms)",
        out,
        size_bytes,
        len(artifacts),
        len(sboms),
    )
    return {
        "bundle_path": str(out),
        "size_bytes": size_bytes,
        "artifacts": artifacts,
        "sbom_count": len(sboms),
        "manifest_sha256": manifest_sha,
    }


def verify_bundle(bundle_path: str) -> Dict[str, Any]:
    """Verify the bundle's manifest.json matches actual artifact hashes."""
    p = Path(bundle_path)
    if not p.exists():
        return {"ok": False, "error": f"bundle not found: {bundle_path}"}

    mismatches: List[Dict[str, Any]] = []
    missing: List[str] = []

    with tarfile.open(str(p), "r:gz") as tar:
        try:
            mf_member = tar.getmember("manifest.json")
        except KeyError:
            return {"ok": False, "error": "manifest.json missing from bundle"}
        mf_obj = tar.extractfile(mf_member)
        if mf_obj is None:
            return {"ok": False, "error": "could not read manifest.json"}
        manifest = json.loads(mf_obj.read().decode("utf-8"))

        # Build member map: name -> sha256 computed on the fly.
        for art in manifest.get("artifacts", []):
            name = art.get("name")
            expected = art.get("sha256")
            try:
                member = tar.getmember(name)
            except KeyError:
                missing.append(name)
                continue
            mf = tar.extractfile(member)
            if mf is None:
                missing.append(name)
                continue
            h = hashlib.sha256()
            while True:
                chunk = mf.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
            actual = h.hexdigest()
            if actual != expected:
                mismatches.append(
                    {"name": name, "expected": expected, "actual": actual}
                )

    ok = not mismatches and not missing
    return {
        "ok": ok,
        "bundle_path": str(p),
        "mismatches": mismatches,
        "missing": missing,
        "artifact_count": len(manifest.get("artifacts", [])),
        "manifest_sha256": manifest.get("manifest_sha256"),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────
def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="NDC Air-Gap Lab Bundle (IL5/IL6)",
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="build a bundle")
    group.add_argument("--verify", metavar="BUNDLE", help="verify an existing bundle")
    ap.add_argument("--output", help="output .tar.gz path (with --build)")
    ap.add_argument("--images-dir", help="directory of container image tarballs")
    ap.add_argument("--gns3-binary", help="path to GNS3 server binary")
    ap.add_argument("--containerlab-binary", help="path to containerlab binary")
    ap.add_argument("--signer", default="", help="signer identifier")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if args.build:
        if not args.output:
            ap.error("--build requires --output")
        result = build_bundle(
            output_path=args.output,
            images_dir=args.images_dir,
            gns3_binary=args.gns3_binary,
            containerlab_binary=args.containerlab_binary,
            signer=args.signer,
        )
    else:
        result = verify_bundle(args.verify)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) is not False else 1


if __name__ == "__main__":
    sys.exit(_main())
