# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Quarantine-first ingest.

``stage(source, ...)`` copies a target artifact (local path / UNC share / ``file://``
URI) into an isolated quarantine directory and records an ``integrity_assessments``
row in ``status='quarantine'`` so every downstream scanner works against the staged
copy, never the original.

SECURITY INVARIANTS (locked design — see plan + docs/features/phase-sipa-software-integrity.md):
  * **Never executes target code.** This module only *copies* bytes. No ``exec``,
    no ``subprocess``, no import of the staged tree. (Remote ``git clone`` lands in
    sipa-ingest-02 with a fixed-arg, ``shell=False`` subprocess — still never run.)
  * **Scheme allowlist.** Only schemes in ``args/integrity_config.yaml``
    ``scheme_allowlist`` are accepted; anything else is rejected with a clear error
    *before* any row is created or any byte copied.
  * **Quarantine-first.** Staging goes to ``<quarantine_dir>/<assessment_id>/`` so a
    HITL gate can release or reject it later.

Source-type auto-detection maps a raw source string to the ``SOURCE_TYPES`` taxonomy
(``local`` / ``git`` / ``unc`` / ``uri``) and an allowlist *scheme*; bare filesystem
paths carry an implicit ``file`` scheme, UNC shares an implicit ``unc`` scheme.

All DB access is via the RLS-aware ``tools.db.storage.get_connection()``; ``tenant_id``
/ ``classification`` / ``created_by`` are stamped from the caller's security context
(``tools.security.security_context.get_security_context``), falling back to the table
defaults (``default`` / ``CUI`` / ``system``) when no context is active.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

import yaml

from tools.integrity.constants import SOURCE_TYPES
from tools.integrity.db.init_db import init_db

# --------------------------------------------------------------------------- #
# Paths / config
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/ingest.py -> repo root
_CONFIG_PATH = BASE_DIR / "args" / "integrity_config.yaml"

# Conservative fallbacks if the config file is missing (matches db-04 skeleton).
_DEFAULT_SCHEME_ALLOWLIST = ["https", "git", "file", "unc"]
_DEFAULT_HOST_ALLOWLIST = ["github.com", "gitlab.com"]
_DEFAULT_QUARANTINE_DIR = ".tmp/integrity_quarantine"


class IngestRejected(ValueError):
    """Raised when a source is refused (disallowed scheme, missing path, etc.).

    Subclasses ``ValueError`` so generic ``except ValueError`` handlers still catch it.
    """


def _load_config() -> dict:
    """Load integrity_config.yaml; tolerate a missing/empty file with defaults."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _scheme_allowlist(cfg: dict) -> set[str]:
    return {str(s).lower() for s in cfg.get("scheme_allowlist", _DEFAULT_SCHEME_ALLOWLIST)}


def _host_allowlist(cfg: dict) -> set[str]:
    return {str(h).lower() for h in cfg.get("host_allowlist", _DEFAULT_HOST_ALLOWLIST)}


def _quarantine_base(cfg: dict) -> Path:
    """Resolve the quarantine root. An env override keeps tests off the repo tree."""
    override = os.environ.get("ICDEV_INTEGRITY_QUARANTINE_DIR")
    if override:
        return Path(override)
    rel = cfg.get("quarantine_dir", _DEFAULT_QUARANTINE_DIR)
    base = Path(rel)
    return base if base.is_absolute() else BASE_DIR / base


# --------------------------------------------------------------------------- #
# Source-type + scheme detection
# --------------------------------------------------------------------------- #
def _detect_source(source: str, override: str, hosts: set[str]) -> tuple[str, str]:
    """Return ``(source_type, scheme)`` for a raw source string.

    ``source_type`` is one of ``SOURCE_TYPES``; ``scheme`` is the value checked
    against the allowlist (``file`` for bare local paths, ``unc`` for UNC shares).
    An explicit ``override`` (anything but ``"auto"``) wins for ``source_type`` but
    the detected ``scheme`` is still enforced.
    """
    s = source.strip()

    # UNC share: \\host\share  (or //host/share)
    if s.startswith("\\\\") or s.startswith("//"):
        source_type, scheme = "unc", "unc"
    else:
        m = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*)://", s)
        if m:
            scheme = m.group(1).lower()
            if scheme == "file":
                source_type = "local"
            elif scheme == "git":
                source_type = "git"
            elif scheme in ("http", "https"):
                host = (urlparse(s).hostname or "").lower()
                source_type = "git" if host in hosts else "uri"
            else:
                source_type = "uri"
        else:
            # Bare filesystem path (Windows drive, POSIX, or relative) — implicit file.
            source_type, scheme = "local", "file"

    if override and override != "auto":
        if override not in SOURCE_TYPES:
            raise IngestRejected(
                f"invalid source_type {override!r}; expected one of {SOURCE_TYPES}"
            )
        source_type = override
    return source_type, scheme


def _resolve_local_path(source: str, source_type: str, scheme: str) -> Path:
    """Resolve a stage-able local filesystem path, or refuse remote schemes.

    ingest-01 stages only file-resolvable sources (local / UNC / ``file://``). Remote
    ``git`` / ``uri`` fetching is the job of sipa-ingest-02 (fixed-arg ``git clone``);
    here it is an explicit, clear refusal rather than a silent no-op.
    """
    s = source.strip()
    if scheme == "file":
        if s.lower().startswith("file://"):
            parsed = urlparse(s)
            local = url2pathname(parsed.path)
            if parsed.netloc and parsed.netloc.lower() != "localhost":
                # file://host/share -> UNC-style path
                return Path(f"//{parsed.netloc}{local}")
            return Path(local)
        return Path(s)
    if source_type == "unc":
        return Path(s)
    raise NotImplementedError(
        f"remote {scheme!r} fetch is handled by sipa-ingest-02 (git clone); "
        "ingest-01 stages local / UNC / file:// sources only"
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _backend_of(conn: Any) -> str:
    """Resolve the backend ('postgresql' | 'sqlite') for a live connection."""
    declared = getattr(conn, "_backend", None)
    if declared:
        return "postgresql" if str(declared).lower().startswith(("postgre", "pg")) else "sqlite"
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    return "postgresql" if backend in ("postgresql", "postgres", "pg") else "sqlite"


_INSERT_SQL = (
    "INSERT INTO integrity_assessments "
    "(source_type, source_ref, mode, project_id, session_id, status, "
    "tenant_id, classification, created_by) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_assessment(conn: Any, params: tuple) -> int:
    """Insert the quarantine assessment row and return its generated PK."""
    if _backend_of(conn) == "postgresql":
        cur = conn.execute(_INSERT_SQL.replace("?", "%s") + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(_INSERT_SQL, params)
    conn.commit()
    return int(cur.lastrowid or 0)


def _caller_context() -> tuple[str, str, str]:
    """(tenant_id, classification, created_by) from the active security context."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    created_by = (getattr(ctx, "user_id", None) or "system") if ctx else "system"
    return tenant_id, classification, created_by


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def stage(
    source: str,
    source_type: str = "auto",
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    conn: Any = None,
) -> dict:
    """Stage ``source`` into quarantine and record an ``integrity_assessments`` row.

    Args:
        source: local path, UNC share (``\\\\host\\share``), or ``file://`` URI.
        source_type: ``"auto"`` (detect) or an explicit value from ``SOURCE_TYPES``.
        project_id / session_id: provenance handles; their presence selects
            ``provenance_aware`` mode (else ``provenance_blind``).
        conn: optional existing DB connection to reuse (e.g. the engine / tests);
            when ``None`` an RLS-aware connection is opened and closed internally.

    Returns:
        ``{"assessment_id": int, "staged_path": str, "source_type": str}``.

    Raises:
        IngestRejected: disallowed scheme, invalid ``source_type``, or missing path.
        NotImplementedError: remote ``git`` / ``uri`` source (handled by ingest-02).

    Never executes the target — bytes are only copied into quarantine.
    """
    cfg = _load_config()
    resolved_type, scheme = _detect_source(source, source_type, _host_allowlist(cfg))

    # 1. Enforce the scheme allowlist BEFORE any row is created or byte copied.
    allowlist = _scheme_allowlist(cfg)
    if scheme not in allowlist:
        raise IngestRejected(
            f"source scheme {scheme!r} is not allowed; allowlist={sorted(allowlist)}"
        )

    # 2. Resolve to a local path (refuses remote schemes) and confirm it exists.
    src_path = _resolve_local_path(source, resolved_type, scheme)
    if not src_path.exists():
        raise IngestRejected(f"source path does not exist: {src_path}")

    # 3. Mode is provenance-aware only when a provenance handle is supplied.
    mode = "provenance_aware" if (project_id or session_id) else "provenance_blind"
    tenant_id, classification, created_by = _caller_context()

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        assessment_id = _insert_assessment(
            conn,
            (
                resolved_type,
                source.strip(),
                mode,
                project_id,
                session_id,
                "quarantine",
                tenant_id,
                classification,
                created_by,
            ),
        )
    finally:
        if own_conn:
            conn.close()

    # 4. Copy into quarantine: <quarantine_dir>/<assessment_id>/.
    dest = _quarantine_base(cfg) / str(assessment_id)
    dest.mkdir(parents=True, exist_ok=True)
    if src_path.is_dir():
        shutil.copytree(src_path, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(src_path, dest / src_path.name)

    return {
        "assessment_id": assessment_id,
        "staged_path": str(dest),
        "source_type": resolved_type,
    }
