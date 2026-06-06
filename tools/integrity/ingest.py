# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Quarantine-first ingest.

``stage(source, ...)`` copies a target artifact (local path / UNC share / ``file://``
URI) into an isolated quarantine directory and records an ``integrity_assessments``
row in ``status='quarantine'`` so every downstream scanner works against the staged
copy, never the original.

SECURITY INVARIANTS (locked design — see plan + docs/features/phase-sipa-software-integrity.md):
  * **Never executes target code.** This module only *copies* bytes (local sources)
    or *clones* a repo (git sources). The single ``subprocess`` call is a fixed-arg,
    ``shell=False`` ``git clone`` of untrusted data — git fires no hooks on clone and
    the staged tree is never imported, ``exec``-ed, or run. ``shell=True`` is banned
    across ``tools/integrity/`` (enforced by a regression test).
  * **Git URL allowlist.** Git sources must be ``https://`` to an allowlisted host
    (``github.com`` / ``gitlab.com``) or a local ``file://`` repo; the URL is
    validated *before* any row is written or any subprocess runs, and embedded
    credentials are redacted from every log line.
  * **Scheme allowlist.** Only schemes in ``args/integrity_config.yaml``
    ``scheme_allowlist`` are accepted; anything else is rejected with a clear error
    *before* any row is created or any byte copied.
  * **Quarantine-first.** Staging goes to ``<quarantine_dir>/<assessment_id>/`` so a
    HITL gate can release or reject it later.
  * **Tamper baseline.** Immediately after staging, a SHA-256 recursive directory
    digest of the quarantined tree is recorded in ``integrity_assessments.dir_digest``
    (via :func:`reverify`'s twin, reusing ``tools/security/blueprint_verifier``).
    :func:`reverify` recomputes it before assessment and writes a ``tamper_mismatch``
    finding if the staged tree changed in between.

Source-type auto-detection maps a raw source string to the ``SOURCE_TYPES`` taxonomy
(``local`` / ``git`` / ``unc`` / ``uri``) and an allowlist *scheme*; bare filesystem
paths carry an implicit ``file`` scheme, UNC shares an implicit ``unc`` scheme.

All DB access is via the RLS-aware ``tools.db.storage.get_connection()``; ``tenant_id``
/ ``classification`` / ``created_by`` are stamped from the caller's security context
(``tools.security.security_context.get_security_context``), falling back to the table
defaults (``default`` / ``CUI`` / ``system``) when no context is active.
"""
from __future__ import annotations

import json
import os
import re
import shutil
# subprocess is used ONLY for a fixed-arg, shell=False ``git clone`` (see
# ``_git_clone``); the cloned target is never executed.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

import yaml

from tools.integrity.constants import SOURCE_TYPES
from tools.integrity.db.init_db import init_db
from tools.security.blueprint_verifier import BlueprintVerifier

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.ingest")

# --------------------------------------------------------------------------- #
# Paths / config
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/ingest.py -> repo root
_CONFIG_PATH = BASE_DIR / "args" / "integrity_config.yaml"

# Conservative fallbacks if the config file is missing (matches db-04 skeleton).
_DEFAULT_SCHEME_ALLOWLIST = ["https", "git", "file", "unc"]
_DEFAULT_HOST_ALLOWLIST = ["github.com", "gitlab.com"]
_DEFAULT_QUARANTINE_DIR = ".tmp/integrity_quarantine"
_DEFAULT_CLONE_DEPTH = 1     # shallow — we assess the current tip, not full history
_DEFAULT_CLONE_TIMEOUT = 120  # seconds; a clone that hangs is killed, not left running


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


def _clone_depth(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("clone_depth", _DEFAULT_CLONE_DEPTH)))
    except (TypeError, ValueError):
        return _DEFAULT_CLONE_DEPTH


def _clone_timeout(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("clone_timeout", _DEFAULT_CLONE_TIMEOUT)))
    except (TypeError, ValueError):
        return _DEFAULT_CLONE_TIMEOUT


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
        f"remote {scheme!r} fetch is not supported; "
        "ingest stages local / UNC / file:// sources and clones git sources only"
    )


# --------------------------------------------------------------------------- #
# Remote git clone — fixed-arg subprocess, shell=False, target NEVER executed
# --------------------------------------------------------------------------- #
def _redact_url(text: str) -> str:
    """Strip ``user:pass@`` credentials from a URL (or any text) before logging.

    ``https://alice:ghp_secret@github.com/org/repo.git`` ->
    ``https://***@github.com/org/repo.git``. Applied to every log line and to any
    git stderr we surface, so tokens embedded in a clone URL never reach a log sink.
    """
    return re.sub(r"://[^/@\s]+@", "://***@", text)


def _validate_git_url(url: str, hosts: set[str]) -> str:
    """Validate a git clone URL against the allowlist; return the cleaned URL.

    Accepts only:
      * ``https://`` URLs whose host is in ``host_allowlist`` (GitHub / GitLab), or
      * ``file://`` URLs (local bare repos / mirrors — no network, used by tests).

    Everything else (``http``, ``git``, ``ssh``, ``ftp``, a bare path, a URL with no
    host, …) is refused with :class:`IngestRejected` *before* any subprocess runs.
    Embedded credentials are permitted (private-repo auth) but never logged.
    """
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()

    if scheme == "file":
        # Local bare repo / mirror: no network, no host to allowlist.
        return url.strip()

    if scheme != "https":
        raise IngestRejected(
            f"git source must use https:// (or file:// for a local repo); "
            f"got {scheme or '<none>'!r} in {_redact_url(url)!r}"
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise IngestRejected(f"git URL has no host: {_redact_url(url)!r}")
    if host not in hosts:
        raise IngestRejected(
            f"git host {host!r} is not allowed; allowlist={sorted(hosts)}"
        )
    return url.strip()


def _git_clone(url: str, dest: Path, depth: int = _DEFAULT_CLONE_DEPTH,
               timeout: int = _DEFAULT_CLONE_TIMEOUT,
               hosts: Optional[set[str]] = None) -> None:
    """Shallow-clone ``url`` into ``dest`` with a FIXED arg list and ``shell=False``.

    SECURITY — read before touching this function:
      * **shell=False, fixed arg list.** The command is assembled as a list and
        handed to the OS directly; the URL is *never* interpolated into a shell
        string. ``shell=True`` must never appear here (a regression test enforces
        the absence of ``shell=True`` across ``tools/integrity/``).
      * ``--`` terminates option parsing so a URL beginning with ``-`` can never be
        read as a git flag (argument-injection defence).
      * ``GIT_TERMINAL_PROMPT=0`` / ``GIT_ASKPASS=true`` make a missing credential a
        fast failure instead of an interactive hang.
      * The clone is re-validated against the allowlist here (defence in depth) so a
        direct caller cannot bypass :func:`_validate_git_url`.
      * The cloned tree is **data only** — it is copied to disk and NEVER executed,
        imported, or run. No git hooks fire on ``clone``.
    """
    if hosts is None:
        hosts = _host_allowlist(_load_config())
    clean_url = _validate_git_url(url, hosts)

    git = shutil.which("git") or "git"
    args = [
        git, "clone",
        "--depth", str(max(1, int(depth))),
        "--no-tags",
        "--single-branch",
        "--",            # end of options: URL/dest can never be parsed as flags
        clean_url,
        str(dest),
    ]

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never prompt for a username/password
    env["GIT_ASKPASS"] = "true"       # and never shell out to an askpass helper

    logger.info("git shallow-clone %s -> %s", _redact_url(clean_url), dest)
    try:
        # Fixed arg list, shell=False, validated URL — safe by construction.
        proc = subprocess.run(  # nosec B603
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:  # git binary absent
        raise IngestRejected("git executable not found on PATH; cannot clone") from exc
    except subprocess.TimeoutExpired as exc:
        raise IngestRejected(
            f"git clone timed out after {timeout}s: {_redact_url(url)}"
        ) from exc

    if proc.returncode != 0:
        detail = _redact_url((proc.stderr or proc.stdout or "").strip())
        raise IngestRejected(
            f"git clone failed (exit {proc.returncode}) for {_redact_url(url)}: {detail}"
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
# Tamper digest — SHA-256 directory baseline (reuses blueprint_verifier)
# --------------------------------------------------------------------------- #
def _dir_digest(directory: Path) -> str:
    """SHA-256 recursive directory digest of a staged tree.

    Reuses ``tools/security/blueprint_verifier.BlueprintVerifier.compute_digest``
    (NemoClaw supply-chain pattern: hash = SHA-256 over sorted ``rel_path + bytes``,
    then SHA-256 over the concatenated per-file hashes; ``.git``/dotfiles/caches are
    skipped so a git clone digests identically to a copied tree).

    ``compute_digest`` is a pure ``os.walk`` + ``hashlib`` computation that touches no
    database, so we instantiate via ``object.__new__`` to skip ``BlueprintVerifier``'s
    ``__init__`` (which would provision the ``blueprint_digests`` table) — staging must
    never write to a digest log just to baseline a tree.
    """
    verifier = object.__new__(BlueprintVerifier)
    result = verifier.compute_digest(Path(directory))
    if "error" in result:
        raise IngestRejected(f"cannot digest staged tree: {result['error']}")
    return str(result["digest"])


def _set_dir_digest(conn: Any, assessment_id: int, digest: str) -> None:
    """Persist the baseline directory digest onto the assessment row."""
    sql = (
        "UPDATE integrity_assessments "
        "SET dir_digest = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    )
    if _backend_of(conn) == "postgresql":
        conn.execute(sql.replace("?", "%s"), (digest, assessment_id))
    else:
        conn.execute(sql, (digest, assessment_id))
    conn.commit()


_FINDING_SQL = (
    "INSERT INTO integrity_findings "
    "(assessment_id, source_scanner, finding_type, severity, file_path, line, "
    "detail, tenant_id, classification) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_finding(conn: Any, params: tuple) -> int:
    """Insert an append-only integrity_findings row and return its generated PK."""
    if _backend_of(conn) == "postgresql":
        cur = conn.execute(_FINDING_SQL.replace("?", "%s") + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(_FINDING_SQL, params)
    conn.commit()
    return int(cur.lastrowid or 0)


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
        source: local path, UNC share (``\\\\host\\share``), ``file://`` URI, or a
            git clone URL (``https://github.com/...`` / ``https://gitlab.com/...``).
        source_type: ``"auto"`` (detect) or an explicit value from ``SOURCE_TYPES``.
        project_id / session_id: provenance handles; their presence selects
            ``provenance_aware`` mode (else ``provenance_blind``).
        conn: optional existing DB connection to reuse (e.g. the engine / tests);
            when ``None`` an RLS-aware connection is opened and closed internally.

    Returns:
        ``{"assessment_id": int, "staged_path": str, "source_type": str}``.

    Raises:
        IngestRejected: disallowed scheme/host, invalid ``source_type``, missing
            path, or a failed/timed-out git clone.
        NotImplementedError: remote ``uri`` (registry pull) source — not yet wired.

    Never executes the target — bytes are only copied (local) or cloned (git) into
    quarantine. A git clone fires no hooks and the cloned tree is treated as data.
    """
    cfg = _load_config()
    hosts = _host_allowlist(cfg)
    resolved_type, scheme = _detect_source(source, source_type, hosts)

    # 1. Enforce the scheme allowlist BEFORE any row is created or byte copied.
    allowlist = _scheme_allowlist(cfg)
    if scheme not in allowlist:
        raise IngestRejected(
            f"source scheme {scheme!r} is not allowed; allowlist={sorted(allowlist)}"
        )

    # 1b. Git sources are shallow-cloned (own path); validate the URL up front so a
    #     disallowed/malformed URL is refused BEFORE any row or subprocess.
    if resolved_type == "git":
        _validate_git_url(source, hosts)
        return _stage_git(source, project_id, session_id, cfg, hosts, conn)

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

        # 4. Copy into quarantine: <quarantine_dir>/<assessment_id>/.
        dest = _quarantine_base(cfg) / str(assessment_id)
        dest.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            shutil.copytree(src_path, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src_path, dest / src_path.name)

        # 5. Baseline the staged tree: SHA-256 directory digest -> dir_digest. A later
        #    reverify() recomputes this to detect tampering between stage and assess.
        digest = _dir_digest(dest)
        _set_dir_digest(conn, assessment_id, digest)
    finally:
        if own_conn:
            conn.close()

    return {
        "assessment_id": assessment_id,
        "staged_path": str(dest),
        "source_type": resolved_type,
        "dir_digest": digest,
    }


def _stage_git(
    source: str,
    project_id: Optional[str],
    session_id: Optional[str],
    cfg: dict,
    hosts: set[str],
    conn: Any = None,
) -> dict:
    """Record a ``git`` assessment row, then shallow-clone the repo into quarantine.

    The URL has already been validated by :func:`stage` (and is re-validated inside
    :func:`_git_clone`). The clone lands at ``<quarantine_dir>/<assessment_id>/`` and
    is treated as inert data — never executed, imported, or run.
    """
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
                "git",
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

        # Clone into <quarantine_dir>/<assessment_id>/. git creates the target dir;
        # only its parent must exist (git refuses to clone into a non-empty dir).
        dest = _quarantine_base(cfg) / str(assessment_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git_clone(
            source.strip(),
            dest,
            depth=_clone_depth(cfg),
            timeout=_clone_timeout(cfg),
            hosts=hosts,
        )

        # Baseline the cloned tree (.git is skipped by the digest, so the digest is
        # stable across re-clones of the same tip and tamper-detectable by reverify()).
        digest = _dir_digest(dest)
        _set_dir_digest(conn, assessment_id, digest)
    finally:
        if own_conn:
            conn.close()

    return {
        "assessment_id": assessment_id,
        "staged_path": str(dest),
        "source_type": "git",
        "dir_digest": digest,
    }


def reverify(assessment_id: int, conn: Any = None) -> dict:
    """Re-baseline a staged tree and flag tampering between stage and assess.

    Recomputes the SHA-256 directory digest of ``<quarantine_dir>/<assessment_id>/``
    and compares it to the ``dir_digest`` recorded by :func:`stage`. If the staged
    tree changed (a file added/modified/removed) — or vanished entirely — an
    append-only ``tamper_mismatch`` finding (scanner ``tamper``, severity
    ``critical``) is written so the scoring stage can quarantine on it.

    The quarantined tree is treated as inert data: this only walks + hashes it, never
    executes it. Idempotent on a clean tree (no finding written when digests match).

    Args:
        assessment_id: the assessment whose staged tree to re-verify.
        conn: optional existing DB connection to reuse; opened/closed internally
            when ``None``.

    Returns:
        ``{"assessment_id", "tampered": bool, "baseline_digest", "current_digest",
        "finding_id": int | None}``. ``tampered`` is ``False`` when no baseline digest
        was recorded (nothing to compare against).

    Raises:
        IngestRejected: no assessment row exists for ``assessment_id``.
    """
    cfg = _load_config()
    staged = _quarantine_base(cfg) / str(assessment_id)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        sel = "SELECT dir_digest FROM integrity_assessments WHERE id = ?"
        if _backend_of(conn) == "postgresql":
            row = conn.execute(sel.replace("?", "%s"), (assessment_id,)).fetchone()
        else:
            row = conn.execute(sel, (assessment_id,)).fetchone()
        if row is None:
            raise IngestRejected(f"no assessment row for id {assessment_id}")
        baseline = row["dir_digest"] if hasattr(row, "keys") else row[0]

        current = _dir_digest(staged) if staged.exists() else None
        # No baseline => nothing to compare; report untampered with both digests null/current.
        tampered = baseline is not None and current != baseline

        finding_id = None
        if tampered:
            tenant_id, classification, _ = _caller_context()
            reason = (
                "staged tree changed between stage and assess"
                if current is not None
                else "staged tree missing at re-verify"
            )
            detail = json.dumps(
                {
                    "expected": baseline,
                    "actual": current,
                    "staged_path": str(staged),
                    "reason": reason,
                }
            )
            finding_id = _insert_finding(
                conn,
                (
                    assessment_id,
                    "tamper",            # source_scanner
                    "tamper_mismatch",   # finding_type
                    "critical",          # severity
                    str(staged),         # file_path
                    None,                # line
                    detail,              # detail (JSON)
                    tenant_id,
                    classification,
                ),
            )
            logger.warning(
                "tamper_mismatch on assessment %s: %s (expected %s, got %s)",
                assessment_id, reason, baseline, current,
            )
    finally:
        if own_conn:
            conn.close()

    return {
        "assessment_id": assessment_id,
        "tampered": tampered,
        "baseline_digest": baseline,
        "current_digest": current,
        "finding_id": finding_id,
    }
