# CUI // SP-CTI
"""tools/airgap/image_vendor — Admin-only container-image vendor for air-gap.

Parallel to ``tools/airgap/wheel_vendor.py`` (PyPI wheels) and
``tools/airgap/driver_vendor.py`` (browser drivers). Those two vendor the
Python and browser halves of an offline install; NOTHING in ``tools/airgap/``
saved or loaded a container image before this module, so "ship a pinned floci
image to the high side" had no mechanism to fit into (measured 2026-09-04).

    low side                                     high side
    --------                                     ---------
    --save --topic floci   -> vendor/images/floci/*.tar ->   --load --topic floci
                                (transport media)             --verify --topic floci

THE SOURCE IS THE LOCAL DAEMON'S IMAGE CACHE, AND NOTHING PULLS
---------------------------------------------------------------
Operator decision 2026-09-05: container-backed floci services reach the LOCAL
Docker daemon on the emulator host. So ``--save`` reads what that daemon
already holds and NEVER reaches a registry. If a pinned digest is absent from
the local cache the run REPORTS that and exits non-zero rather than fetching
it: a vendor that pulls on demand cannot run on the disconnected side it
exists to serve, and it would turn a missing pin into a silent network fetch
whose result nobody pinned.

That is enforced structurally, not by convention. Every docker invocation goes
through ``_docker()``, which refuses any subcommand outside
``ALLOWED_DOCKER_COMMANDS`` — a frozenset that does not contain ``pull``,
``fetch`` or ``run``. ``tests/airgap/test_image_vendor.py`` reads this module's
AST to prove ``subprocess`` is reached from nowhere else.

A PIN IS A DIGEST, NEVER A TAG
------------------------------
``floci/floci:2.0.1`` is mutable — the registry can move it, and an air-gap
bundle whose contents depend on when it was built is not evidence of anything.
Pin files hold ``repo@sha256:<64 hex>`` and ``parse_pin`` REFUSES a bare tag.

WHAT ``--verify`` ACTUALLY PROVES, AND WHY IT DOES NOT NEED DOCKER
------------------------------------------------------------------
"A verification that only checks the file exists proves nothing about what is
in it." Measured on this host (Docker 28.5.1) 2026-09-05: ``docker save`` emits
an **OCI layout** tar, in which

  * every blob under ``blobs/sha256/`` is named by its own sha256, and
  * ``index.json`` records the top manifest digest — the very digest a
    ``repo@sha256:…`` reference names (confirmed byte-equal to the RepoDigest).

So ``verify_bundle`` re-hashes every blob against its filename and compares
``index.json``'s manifest digest to the pin. That is a cryptographic proof the
tar contains exactly the pinned image, and it runs with no daemon at all —
which matters, because the high side may verify media before it has anywhere
to load it.

An older engine writes the legacy ``docker-v1`` layout (``manifest.json`` plus
``<hash>.json``, no ``index.json``). There the manifest digest is simply not
recorded, so ``manifest_digest_verified`` is **None with a reason**, never
True and never False. Reporting an unverifiable layout as verified is the
defect this whole module is written against.

THREE STATUSES, NEVER MERGED
----------------------------
``verified``    the check ran and passed.
``failed``      the check ran and FAILED. A real finding.
``unmeasured``  the check could not run — no docker CLI, no bucket, a legacy
                layout. NEVER a clean bundle. A host with no docker reports
                that it could not measure; it does not report success.

WHY THIS DOES NOT REFUSE TO RUN IN AIR-GAP (wheel_vendor DOES)
--------------------------------------------------------------
``wheel_vendor`` calls ``pip download`` and so hard-fails under ``is_airgap()``
— the fetch could only fail there. ``docker save`` reads a local cache and
touches no network, so the same refusal here would be FABRICATED: it would
block the one machine most likely to need to re-cut a bundle from images it
already holds. ``--load`` and ``--verify`` are high-side acts by definition.

Usage::

    python tools/airgap/image_vendor.py --save --topic floci --json
    python tools/airgap/image_vendor.py --verify --topic floci --json
    python tools/airgap/image_vendor.py --load --topic floci --json
    python tools/airgap/image_vendor.py --list --json
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

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

logger = get_logger("icdev.airgap.image_vendor")

# The ONE root resolver (xit-decl-03). NOT `Path(__file__).parents[2]` the way
# wheel_vendor.py still spells it: that is a private, hard-coded claim about
# where this file sits, true today and silently wrong the moment it moves.
BASE_DIR = repo_root(__file__)
IMAGES_DIR = BASE_DIR / "vendor" / "images"

# ── Statuses. Kept apart on purpose; see the module docstring. ─────────────
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_UNMEASURED = "unmeasured"

# ── The only docker subcommands this module may ever run. ──────────────────
#
# `pull` is absent BY DESIGN and its absence is asserted by a test. So are
# `run`, `rmi` and `tag`: this tool moves bytes between a local cache and a
# tar, and nothing else. `_docker()` refuses anything not listed here, so a
# future edit adding a pull has to change this line, in review, on purpose.
ALLOWED_DOCKER_COMMANDS = frozenset({"version", "image", "save", "load"})

# repo@sha256:<64 hex>. The repo half may carry a registry host and a port.
_PIN_RE = re.compile(r"^(?P<repo>[A-Za-z0-9][^@\s]*)@(?P<algo>sha256):(?P<hex>[0-9a-f]{64})$")

_BLOB_PREFIX = "blobs/sha256/"


# ---------------------------------------------------------------------------
# Pin parsing — a digest, never a tag
# ---------------------------------------------------------------------------


def parse_pin(line: str) -> dict[str, str]:
    """Parse one pin line into ``{ref, repo, algo, digest}``.

    Raises ``ValueError`` on a tag reference. A tag is mutable, so a bundle
    built from one cannot be shown to contain what was intended.
    """
    ref = line.strip()
    match = _PIN_RE.match(ref)
    if not match:
        hint = ""
        if "@" not in ref:
            hint = (
                " — this looks like a TAG. A tag is mutable and an air-gap "
                "bundle must be provable. Resolve it to a digest first: "
                "docker image inspect <ref> --format '{{index .RepoDigests 0}}'"
            )
        raise ValueError(f"not a pinned digest reference: {ref!r}{hint}")
    return {
        "ref": ref,
        "repo": match.group("repo"),
        "algo": match.group("algo"),
        "digest": f"{match.group('algo')}:{match.group('hex')}",
    }


def pin_file(topic: str) -> Path:
    return IMAGES_DIR / f"images-{topic}.txt"


def read_pins(topic: str) -> list[dict[str, str]]:
    """Read ``vendor/images/images-<topic>.txt``.

    Blank lines and ``#`` comments are skipped. Every remaining line must be a
    digest reference; one bad line fails the whole read rather than vendoring
    a partial bundle whose gaps nobody would notice.
    """
    path = pin_file(topic)
    if not path.exists():
        raise FileNotFoundError(
            f"No pin file for topic={topic!r}: expected {path} — "
            f"create it with digest-pinned entries (repo@sha256:…) before --save"
        )
    pins: list[dict[str, str]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            pins.append(parse_pin(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from exc
    if not pins:
        raise ValueError(f"{path} declares no image pins")
    return pins


def _bucket_dir(topic: str) -> Path:
    return IMAGES_DIR / topic


def tar_name_for(pin: dict[str, str]) -> str:
    """A filesystem-safe, collision-free tar name for a pin.

    ``<repo with / and : replaced>@<first 12 of digest>.tar``. The short digest
    keeps two pins of the same repo apart; the full digest lives in the
    bundle manifest and in the tar itself, so the filename is a label and
    never the evidence.
    """
    safe_repo = re.sub(r"[^A-Za-z0-9._-]", "_", pin["repo"])
    short = pin["digest"].split(":", 1)[1][:12]
    return f"{safe_repo}@{short}.tar"


# ---------------------------------------------------------------------------
# Docker access — one door, allowlisted, never pulls
# ---------------------------------------------------------------------------


def _docker(args: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    """Run one allowlisted docker subcommand.

    The allowlist is the point: it is what makes "this tool never pulls" a
    property of the code rather than a claim in a docstring.
    """
    if not args or args[0] not in ALLOWED_DOCKER_COMMANDS:
        raise RuntimeError(
            f"refusing docker subcommand {args[0] if args else '<empty>'!r}: "
            f"image_vendor may only run {sorted(ALLOWED_DOCKER_COMMANDS)}. "
            f"It never pulls — the local image cache is the source of record."
        )
    return subprocess.run(  # noqa: S603
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def docker_basis() -> dict[str, Any]:
    """Can we talk to a daemon at all? Returns the REASON when we cannot.

    ``available`` False is what makes every docker-dependent result
    ``unmeasured`` instead of a fabricated pass.
    """
    try:
        proc = _docker(["version", "--format", "{{.Server.Version}}"], timeout=30)
    except FileNotFoundError:
        return {"available": False, "reason": "docker CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "docker version timed out"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "reason": f"docker unusable: {exc}"}
    if proc.returncode != 0:
        return {
            "available": False,
            "reason": f"docker daemon unreachable: {(proc.stderr or proc.stdout).strip()[:200]}",
        }
    return {"available": True, "server_version": proc.stdout.strip()}


def local_image_present(ref: str) -> dict[str, Any]:
    """Is ``ref`` in the LOCAL image cache? Never pulls.

    ``docker image inspect`` is local-only — measured 2026-09-05, an absent
    reference returns ``No such image`` with no network access and leaves
    nothing behind.
    """
    proc = _docker(["image", "inspect", ref, "--format", "{{.Id}}"], timeout=60)
    if proc.returncode != 0:
        return {
            "present": False,
            "reason": (proc.stderr or proc.stdout).strip()[:200] or "no such image",
        }
    return {"present": True, "image_id": proc.stdout.strip()}


# ---------------------------------------------------------------------------
# Tar-level verification — the strong proof, and it needs no daemon
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _index_manifest_digests(tar: tarfile.TarFile) -> list[str] | None:
    """Digests named by an OCI-layout ``index.json``; None if not that layout."""
    try:
        member = tar.getmember("index.json")
    except KeyError:
        return None
    handle = tar.extractfile(member)
    if handle is None:
        return None
    try:
        index = json.loads(handle.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    digests = [
        entry["digest"]
        for entry in index.get("manifests", [])
        if isinstance(entry, dict) and entry.get("digest")
    ]
    return digests


def verify_bundle(tar_path: Path, expected_digest: str | None = None) -> dict[str, Any]:
    """Verify a saved image tar WITHOUT docker.

    Two independent checks:

    * every ``blobs/sha256/<name>`` re-hashes to ``<name>`` — content
      addressing, so any altered byte anywhere in the image surfaces here;
    * ``index.json``'s manifest digest equals ``expected_digest`` — the tar
      holds the image that was PINNED, not merely a self-consistent one.

    The second check is what a legacy ``docker-v1`` tar cannot support, and
    there it reports None with a reason rather than passing.
    """
    result: dict[str, Any] = {
        "tar": str(tar_path),
        "exists": tar_path.exists(),
        "layout": None,
        "blobs_checked": 0,
        "blobs_corrupt": [],
        "manifest_digest_in_tar": None,
        "manifest_digest_verified": None,
        "manifest_digest_reason": None,
        "status": STATUS_UNMEASURED,
        "ok": False,
    }
    if not tar_path.exists():
        result["manifest_digest_reason"] = "tar missing"
        return result

    try:
        with tarfile.open(tar_path, "r:*") as tar:
            names = set(tar.getnames())
            result["layout"] = "oci" if "index.json" in names else "docker-v1"

            for member in tar.getmembers():
                if not member.isfile() or not member.name.startswith(_BLOB_PREFIX):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
                expected_name = member.name[len(_BLOB_PREFIX):]
                result["blobs_checked"] += 1
                if digest.hexdigest() != expected_name:
                    result["blobs_corrupt"].append(
                        {"blob": expected_name, "actual": digest.hexdigest()}
                    )

            digests = _index_manifest_digests(tar)
    except (tarfile.TarError, OSError) as exc:
        result["manifest_digest_reason"] = f"unreadable tar: {exc}"
        result["status"] = STATUS_FAILED
        return result

    if digests is None:
        result["manifest_digest_reason"] = (
            "legacy docker-v1 layout records no manifest digest — the pin "
            "cannot be confirmed from this tar. Re-save on an engine that "
            "writes the OCI layout to obtain a provable bundle."
        )
    else:
        result["manifest_digest_in_tar"] = digests
        if expected_digest is None:
            result["manifest_digest_reason"] = "no expected digest supplied"
        elif expected_digest in digests:
            result["manifest_digest_verified"] = True
        else:
            result["manifest_digest_verified"] = False
            result["manifest_digest_reason"] = (
                f"tar holds {digests} — pinned {expected_digest}"
            )

    if result["blobs_corrupt"] or result["manifest_digest_verified"] is False:
        result["status"] = STATUS_FAILED
    elif result["blobs_checked"] == 0:
        # A tar with no blobs proves nothing about an image.
        result["status"] = STATUS_UNMEASURED
        result["manifest_digest_reason"] = (
            result["manifest_digest_reason"] or "tar contains no content-addressed blobs"
        )
    elif result["manifest_digest_verified"] is True:
        result["status"] = STATUS_VERIFIED
    else:
        result["status"] = STATUS_UNMEASURED

    result["ok"] = result["status"] == STATUS_VERIFIED
    return result


# ---------------------------------------------------------------------------
# Bundle manifest (SHA256SUM + MANIFEST.json), same spirit as vendor/wheels/
# ---------------------------------------------------------------------------


def _write_bundle_manifest(bucket: Path, entries: list[dict[str, Any]]) -> None:
    lines = [f"{e['tar_sha256']}  {e['tar_name']}\n" for e in entries]
    (bucket / "SHA256SUM").write_text("".join(lines), encoding="utf-8", newline="")
    (bucket / "MANIFEST.json").write_text(
        json.dumps({"images": entries}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_bundle_manifest(bucket: Path) -> list[dict[str, Any]]:
    path = bucket / "MANIFEST.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("images", [])
    except (ValueError, OSError):
        return []


# ---------------------------------------------------------------------------
# save / load / verify / list
# ---------------------------------------------------------------------------


def save(topic: str) -> dict[str, Any]:
    """``docker save`` every pinned digest from the LOCAL cache into the bucket.

    A pin absent from the local cache is reported under
    ``absent_from_local_cache`` and makes the run fail. Nothing is fetched.
    """
    pins = read_pins(topic)
    basis = docker_basis()
    out: dict[str, Any] = {
        "topic": topic,
        "bucket": str(_bucket_dir(topic)),
        "docker": basis,
        "pins": [p["ref"] for p in pins],
        "saved": [],
        "absent_from_local_cache": [],
        "errors": [],
    }
    if not basis["available"]:
        out["status"] = STATUS_UNMEASURED
        out["ok"] = False
        out["note"] = (
            "no usable docker daemon — could not measure the local image cache. "
            "This is NOT an empty or clean bundle."
        )
        return out

    bucket = _bucket_dir(topic)
    bucket.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for pin in pins:
        presence = local_image_present(pin["ref"])
        if not presence["present"]:
            out["absent_from_local_cache"].append(
                {"ref": pin["ref"], "reason": presence["reason"]}
            )
            continue

        tar_name = tar_name_for(pin)
        tar_path = bucket / tar_name
        logger.info("docker save %s -> %s", pin["ref"], tar_path)
        proc = _docker(["save", pin["ref"], "-o", str(tar_path)])
        if proc.returncode != 0:
            out["errors"].append(
                {"ref": pin["ref"], "error": (proc.stderr or proc.stdout).strip()[:300]}
            )
            continue

        bundle = verify_bundle(tar_path, pin["digest"])
        entries.append({
            "ref": pin["ref"],
            "repo": pin["repo"],
            "digest": pin["digest"],
            "image_id": presence.get("image_id"),
            "tar_name": tar_name,
            "tar_sha256": _sha256_file(tar_path),
            "bytes": tar_path.stat().st_size,
            "layout": bundle["layout"],
            "blobs_checked": bundle["blobs_checked"],
            "bundle_status": bundle["status"],
            "manifest_digest_verified": bundle["manifest_digest_verified"],
            "manifest_digest_reason": bundle["manifest_digest_reason"],
        })

    out["saved"] = entries
    if entries:
        _write_bundle_manifest(bucket, entries)

    failed = out["absent_from_local_cache"] or out["errors"]
    unproven = [e for e in entries if e["bundle_status"] != STATUS_VERIFIED]
    if failed:
        out["status"] = STATUS_FAILED
    elif not entries:
        out["status"] = STATUS_UNMEASURED
    elif unproven:
        out["status"] = STATUS_UNMEASURED
    else:
        out["status"] = STATUS_VERIFIED
    out["ok"] = out["status"] == STATUS_VERIFIED
    return out


def verify(topic: str, *, probe_daemon: bool = True) -> dict[str, Any]:
    """Verify the bundle on disk, and optionally corroborate with the daemon.

    The tar-level proof is authoritative and runs with no docker. The daemon
    probe answers a different question — "has this host loaded it yet" — and
    is reported apart so neither can be mistaken for the other.
    """
    bucket = _bucket_dir(topic)
    out: dict[str, Any] = {
        "topic": topic,
        "bucket": str(bucket),
        "images": [],
        "errors": [],
    }
    if not bucket.exists():
        out["status"] = STATUS_UNMEASURED
        out["ok"] = False
        out["note"] = f"bucket missing: {bucket} — nothing vendored for this topic"
        return out

    entries = read_bundle_manifest(bucket)
    if not entries:
        out["status"] = STATUS_UNMEASURED
        out["ok"] = False
        out["note"] = "MANIFEST.json missing or empty — cannot tell what this bucket should hold"
        return out

    basis = docker_basis() if probe_daemon else {"available": False, "reason": "daemon probe skipped"}
    out["docker"] = basis

    for entry in entries:
        tar_path = bucket / entry["tar_name"]
        bundle = verify_bundle(tar_path, entry.get("digest"))

        tar_sha_ok: bool | None = None
        if tar_path.exists() and entry.get("tar_sha256"):
            tar_sha_ok = _sha256_file(tar_path) == entry["tar_sha256"]

        daemon_present: bool | None = None
        daemon_reason: str | None = None
        if basis["available"]:
            presence = local_image_present(entry["ref"])
            daemon_present = bool(presence["present"])
            if not presence["present"]:
                daemon_reason = presence["reason"]
        else:
            daemon_reason = basis["reason"]

        out["images"].append({
            "ref": entry["ref"],
            "digest": entry.get("digest"),
            "tar_name": entry["tar_name"],
            "tar_sha256_matches_manifest": tar_sha_ok,
            "bundle": bundle,
            "in_local_daemon": daemon_present,
            "daemon_reason": daemon_reason,
            "status": bundle["status"] if tar_sha_ok is not False else STATUS_FAILED,
        })

    statuses = {img["status"] for img in out["images"]}
    if STATUS_FAILED in statuses:
        out["status"] = STATUS_FAILED
    elif statuses == {STATUS_VERIFIED}:
        out["status"] = STATUS_VERIFIED
    else:
        out["status"] = STATUS_UNMEASURED
    out["ok"] = out["status"] == STATUS_VERIFIED
    return out


def load(topic: str) -> dict[str, Any]:
    """``docker load`` every tar in the bucket, then verify by digest.

    Order matters: the bundle is verified BEFORE it is loaded, so a corrupt or
    substituted tar is refused rather than imported and then complained about.
    """
    bucket = _bucket_dir(topic)
    out: dict[str, Any] = {
        "topic": topic,
        "bucket": str(bucket),
        "loaded": [],
        "refused": [],
        "errors": [],
    }
    if not bucket.exists():
        out["status"] = STATUS_UNMEASURED
        out["ok"] = False
        out["note"] = f"bucket missing: {bucket}"
        return out

    entries = read_bundle_manifest(bucket)
    if not entries:
        out["status"] = STATUS_UNMEASURED
        out["ok"] = False
        out["note"] = "MANIFEST.json missing or empty — refusing to load unidentified tars"
        return out

    basis = docker_basis()
    out["docker"] = basis
    if not basis["available"]:
        out["status"] = STATUS_UNMEASURED
        out["ok"] = False
        out["note"] = (
            "no usable docker daemon — nothing was loaded and nothing was measured. "
            "This is NOT a clean load."
        )
        return out

    for entry in entries:
        tar_path = bucket / entry["tar_name"]
        bundle = verify_bundle(tar_path, entry.get("digest"))
        if bundle["status"] == STATUS_FAILED:
            out["refused"].append({
                "ref": entry["ref"],
                "reason": "bundle verification FAILED before load",
                "bundle": bundle,
            })
            continue

        proc = _docker(["load", "-i", str(tar_path)])
        if proc.returncode != 0:
            out["errors"].append({
                "ref": entry["ref"],
                "error": (proc.stderr or proc.stdout).strip()[:300],
            })
            continue

        # Verify BY DIGEST after load. Three-valued on purpose: an engine whose
        # image store does not index a digest-saved image by its manifest
        # digest cannot answer, and "cannot answer" is not "wrong". The tar
        # proof above already established what the bytes are.
        presence = local_image_present(entry["ref"])
        if presence["present"]:
            digest_verified: bool | None = True
            digest_reason = None
        elif bundle["manifest_digest_verified"] is True:
            digest_verified = None
            digest_reason = (
                "daemon does not resolve this digest reference after load "
                f"({presence['reason']}); the bundle's own digest proof stands"
            )
        else:
            digest_verified = False
            digest_reason = presence["reason"]

        out["loaded"].append({
            "ref": entry["ref"],
            "digest": entry.get("digest"),
            "tar_name": entry["tar_name"],
            "load_output": proc.stdout.strip()[:200],
            "bundle_status": bundle["status"],
            "digest_verified_in_daemon": digest_verified,
            "digest_reason": digest_reason,
        })

    if out["refused"] or out["errors"] or any(
        img["digest_verified_in_daemon"] is False for img in out["loaded"]
    ):
        out["status"] = STATUS_FAILED
    elif out["loaded"] and all(
        img["digest_verified_in_daemon"] is True and img["bundle_status"] == STATUS_VERIFIED
        for img in out["loaded"]
    ):
        out["status"] = STATUS_VERIFIED
    else:
        out["status"] = STATUS_UNMEASURED
    out["ok"] = out["status"] == STATUS_VERIFIED
    return out


def list_bundles() -> dict[str, Any]:
    """List every topic pin file and every vendored bucket."""
    out: dict[str, Any] = {"images_dir": str(IMAGES_DIR), "topics": [], "buckets": []}
    if not IMAGES_DIR.exists():
        return out
    out["topics"] = sorted(
        p.stem.replace("images-", "") for p in IMAGES_DIR.glob("images-*.txt")
    )
    for sub in sorted(IMAGES_DIR.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        tars = sorted(t.name for t in sub.glob("*.tar"))
        out["buckets"].append({
            "topic": sub.name,
            "tars": tars,
            "tar_count": len(tars),
            "has_manifest": (sub / "MANIFEST.json").exists(),
            "has_sha256sum": (sub / "SHA256SUM").exists(),
            "bytes": sum((sub / t).stat().st_size for t in tars),
        })
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="ICDEV™ air-gap container-image vendor (admin-only). "
                    "Saves PINNED DIGESTS from the local image cache; never pulls.",
    )
    parser.add_argument("--save", action="store_true", help="docker save the pins for --topic")
    parser.add_argument("--load", action="store_true", help="docker load the bucket for --topic")
    parser.add_argument("--verify", action="store_true", help="Verify the bucket for --topic")
    parser.add_argument("--list", action="store_true", help="List pin topics + vendored buckets")
    parser.add_argument("--topic", type=str, help="Bundle topic (vendor/images/images-<topic>.txt)")
    parser.add_argument(
        "--no-daemon-probe", action="store_true",
        help="--verify only: skip the docker corroboration and verify the tars alone",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.list:
            out = list_bundles()
        elif args.save:
            if not args.topic:
                parser.error("--save requires --topic")
            out = save(args.topic)
        elif args.load:
            if not args.topic:
                parser.error("--load requires --topic")
            out = load(args.topic)
        elif args.verify:
            if not args.topic:
                parser.error("--verify requires --topic")
            out = verify(args.topic, probe_daemon=not args.no_daemon_probe)
        else:
            parser.print_help()
            return 2
    except Exception as exc:
        payload = {"ok": False, "status": STATUS_FAILED, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(out, indent=2))
    # `ok` is reserved for a MEASURED pass. `unmeasured` exits 2 so a caller
    # can never read "could not measure" as "clean" the way exit 0 would let it.
    if out.get("ok", True):
        return 0
    return 2 if out.get("status") == STATUS_UNMEASURED else 1


if __name__ == "__main__":
    sys.exit(_main())
