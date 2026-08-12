#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — the on-disk shape of an agent-session case bundle.

This module holds ONLY the format constants and the three digest recipes a
verifier needs. It deliberately contains no database access: a case bundle is
verified from the bundle alone, on a machine that never had the source
database. That is the whole point of a portable forensic bundle.

Bundle layout::

    <bundle>/
      manifest.json                 # member list + SHA-256 of every other file
      records/hook_events.json      # {"table": ..., "records": [...]}
      records/audit_trail.json      # {"table": ..., "records": [...], "chain_context": {...}}
      ...                           # any other member; manifest-verified only

The three recipes, and where each MUST stay in step with its writer:

1. ``sha256_file`` — plain SHA-256 over file bytes, recorded in the manifest.
2. ``compute_event_hmac`` — HMAC-SHA256 over the ``hook_events.payload`` text,
   identical to ``.claude/hooks/send_event.py::compute_hmac``. That file is not
   importable (``.claude`` is not a package name), so the two lines are
   duplicated here and pinned by ``tests/test_agov_case_bundle_verifier.py``.
3. ``compute_audit_row_hash`` — the ``audit_trail.hash`` recipe for the
   migration-149 chain columns. Unlike the two above it is NOT duplicated here:
   it is re-exported from ``tools/audit/row_hash.py``, the single definition the
   chain writer and ``provenance_verifier.py`` also call. That module is
   stdlib-only and touches no database, so importing it costs this one nothing.

Usage:
    from tools.agent_case.bundle_format import build_manifest, write_manifest
"""

import hashlib
import hmac
import json
from pathlib import Path

from icdev.tools.audit import row_hash as _row_hash

# --- Manifest --------------------------------------------------------------

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
DIGEST_ALGORITHM = "sha256"

# Well-known members. Anything else in the bundle is manifest-verified only.
HOOK_EVENTS_MEMBER = "records/hook_events.json"
AUDIT_TRAIL_MEMBER = "records/audit_trail.json"

# --- hook_events HMAC ------------------------------------------------------

HMAC_SECRET_ENV = "ICDEV_HOOK_HMAC_SECRET"

# The literal fallback in .claude/hooks/send_event.py::store_event. A signature
# that verifies against THIS value proves nothing about authenticity — anyone
# reading the repository can forge it. Callers must treat it as "not verified",
# never as "passed".
DEFAULT_HOOK_HMAC_SECRET = "icdev-default-hmac-key"

# --- audit_trail hash chain (migration 149) --------------------------------
#
# Re-exported, not redefined. tools/audit/row_hash.py is the single definition
# the chain writer and provenance_verifier.py also call; a copy here would drift
# and report tampering on every row. These four names stay part of this module's
# published surface because a bundle verifier imports them from here.

AUDIT_HASH_FIELDS = _row_hash.AUDIT_HASH_FIELDS
GENESIS_HASH = _row_hash.GENESIS_HASH
audit_row_content = _row_hash.audit_row_content
compute_audit_row_hash = _row_hash.compute_audit_row_hash


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    """SHA-256 hex digest of a file, read in binary in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_event_hmac(payload: str, secret: str) -> str:
    """HMAC-SHA256 of a hook_events payload.

    MUST stay byte-identical to .claude/hooks/send_event.py::compute_hmac.
    ``payload`` is the raw ``hook_events.payload`` text as stored — re-serializing
    the parsed JSON would change key order/spacing and break every signature.
    """
    return hmac.new(secret.encode(), (payload or "").encode(), hashlib.sha256).hexdigest()


def is_safe_member_path(member_path: str) -> bool:
    """True if a manifest member path stays inside the bundle directory.

    A manifest is attacker-controlled input: the verifier must never resolve
    ``../../etc/passwd`` or ``C:\\Windows\\...`` just because a member said so.
    """
    if not member_path or not isinstance(member_path, str):
        return False
    if member_path.startswith(("/", "\\")):
        return False
    if ":" in member_path:  # drive letters and NTFS alternate data streams
        return False
    parts = member_path.replace("\\", "/").split("/")
    return not any(p in ("..", "") for p in parts)


def iter_bundle_files(bundle_dir):
    """Yield (relative posix path, Path) for every file except the manifest."""
    bundle_dir = Path(bundle_dir)
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        if rel == MANIFEST_NAME:
            continue
        yield rel, path


def build_manifest(bundle_dir, bundle_id: str = None, session_id: str = None,
                   created_at: str = None, extra: dict = None) -> dict:
    """Build a manifest dict covering every file in ``bundle_dir``.

    The manifest never covers itself — it is the root of trust, and a
    self-referential digest is not computable.
    """
    members = [
        {"path": rel, "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for rel, path in iter_bundle_files(bundle_dir)
    ]
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "algorithm": DIGEST_ALGORITHM,
        "bundle_id": bundle_id,
        "session_id": session_id,
        "created_at": created_at,
        "classification": "CUI",
        "members": members,
    }
    if extra:
        manifest.update(extra)
    return manifest


def write_manifest(bundle_dir, manifest: dict) -> Path:
    """Write ``manifest.json`` into the bundle. Returns the path written."""
    path = Path(bundle_dir) / MANIFEST_NAME
    # newline="\n": a bundle written on Windows and verified on Linux must be
    # byte-identical, and every member digest is over raw bytes.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True))
    return path


def read_manifest(bundle_dir) -> dict:
    """Read and parse ``manifest.json``. Raises on missing/invalid JSON."""
    path = Path(bundle_dir) / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))
