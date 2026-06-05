# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Scanner adapters.

Thin adapters that **shell out** to ICDEV's existing static scanners and fold
their heterogeneous output into the single ``integrity_findings`` shape
(``source_scanner``, ``finding_type``, ``severity``, ``file_path``, ``line``,
``detail``). The adapters never reimplement a scanner — they invoke the proven
ones and normalize:

  * ``tools/security/sast_runner.py``       — multi-language SAST (bandit / gosec /
        spotbugs / clippy / eslint-security / security-code-scan) via ``run_sast``;
        emits ``source_scanner='sast'``, ``finding_type='dangerous_api'``.
  * ``tools/security/secret_detector.py``   — detect-secrets (with a deterministic
        built-in pattern fallback); ``source_scanner='secrets'``,
        ``finding_type='secret'``.
  * ``tools/security/dependency_auditor.py``— pip-audit / npm-audit / etc. SCA;
        ``source_scanner='deps'``, ``finding_type='vuln_dependency'``.
  * ``tools/analysis/formal_verifier.py``   — property checks (SQLi immunity,
        dangerous patterns, input validation); ``source_scanner='formal'``,
        ``finding_type='dangerous_api'``. Opt-in (slow); off by default.
  * ``tools/security/container_scanner.py`` — Dockerfile analysis + trivy image
        scan; ``source_scanner='container'``. Runs **only** when a Dockerfile/image
        is present in the quarantined tree (else a clean no-op). Opt-in; off by
        default.

SECURITY INVARIANTS (consistent with ingest.py — the SIPA static-only contract):
  * **Never executes target code.** Each adapter runs a *scanner* against the
    quarantined tree in a **separate process** (``subprocess``, ``shell=False``,
    fixed arg list). The scanners are static analyzers — they read the staged
    bytes, they do not import/exec the target. Process isolation means a scanner
    crash or hang can never take SIPA down (per-scanner timeout kills it).
  * **Append-only persistence.** Findings are written to ``integrity_findings``
    via the same RLS-aware path as ingest (``_insert_finding`` / ``get_connection``)
    and are never updated/deleted (enforced at the hook layer).
  * **Severity is single-sourced.** Every scanner's native severity vocabulary is
    mapped onto ``constants.SEVERITY`` (``critical`` > ``high`` > ``medium`` >
    ``low`` > ``info``) by :func:`_norm_severity`; an unknown value never reaches
    the DB (it falls back to the adapter's default and so always satisfies the
    ``CHECK (severity IN (...))`` constraint).

The scanner subprocess seam is :func:`_invoke_scanner` — tests monkeypatch it to
feed canned scanner JSON, exercising normalization + persistence deterministically
without needing pip-audit / bandit / the network present.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 — fixed-arg, shell=False scanner invocation (see _invoke_scanner)
import sys
from pathlib import Path
from typing import Any, Optional

from tools.integrity.constants import SEVERITY
from tools.integrity.db.init_db import init_db

# Reuse ingest's persistence + context helpers so the findings INSERT and the
# tenant/classification stamping can never drift between the two writers.
from tools.integrity.ingest import (
    BASE_DIR,
    _caller_context,
    _insert_finding,
    _load_config,
    _quarantine_base,
)

logger = logging.getLogger("icdev.integrity.scanners")

# --------------------------------------------------------------------------- #
# Existing scanners we orchestrate (canonical paths). dependency_auditor lives
# under tools/security/ (the config comment's tools/supply_chain path is stale).
# --------------------------------------------------------------------------- #
_SAST_RUNNER = BASE_DIR / "tools" / "security" / "sast_runner.py"
_SECRET_DETECTOR = BASE_DIR / "tools" / "security" / "secret_detector.py"
_DEPENDENCY_AUDITOR = BASE_DIR / "tools" / "security" / "dependency_auditor.py"
_CONTAINER_SCANNER = BASE_DIR / "tools" / "security" / "container_scanner.py"
# formal_verifier exposes multi-check verify_project(); reached via `python -c`
# (the module CLI is per-file/dir but we want the aggregated project sweep).

# Per-scanner subprocess wall-clock cap (seconds). A scan that hangs is killed,
# never left running. Overridable via integrity_config.yaml ``scan_timeout``.
_DEFAULT_SCAN_TIMEOUT = 360

# --------------------------------------------------------------------------- #
# Severity normalization — fold every scanner's native scale into SEVERITY.
# --------------------------------------------------------------------------- #
# constants.SEVERITY == ["critical", "high", "medium", "low", "info"].
_SEVERITY_ALIASES = {
    "moderate": "medium",   # npm audit
    "warning": "medium",    # clippy / generic linters
    "error": "high",        # clippy / msbuild
    "note": "low",          # clippy
    "help": "low",          # clippy
    "unknown": "info",      # pip-audit when CVSS absent
    "informational": "info",
    "": "info",
}


def _norm_severity(raw: Any, default: str = "info") -> str:
    """Map a scanner's native severity onto ``constants.SEVERITY``.

    Lowercases, applies the alias table (``moderate``->``medium`` etc.), and
    clamps anything still unrecognized to ``default`` so the value always
    satisfies the ``integrity_findings.severity`` CHECK constraint.
    """
    if raw is None:
        return default if default in SEVERITY else "info"
    s = str(raw).strip().lower()
    s = _SEVERITY_ALIASES.get(s, s)
    if s in SEVERITY:
        return s
    return default if default in SEVERITY else "info"


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #
def _staged_dir(assessment_id: int, staged_path: Optional[str]) -> Path:
    """Resolve the quarantined tree for an assessment.

    Explicit ``staged_path`` wins (engine/tests); otherwise it is derived from the
    quarantine base + assessment id, mirroring ``ingest.stage``'s layout.
    """
    if staged_path:
        return Path(staged_path)
    return _quarantine_base(_load_config()) / str(assessment_id)


def _rel(path: Any, root: Path) -> Optional[str]:
    """Best-effort path relative to the staged root (portable across machines).

    Returns the original string if it is outside ``root`` or on another drive
    (relpath raises on Windows cross-drive); ``None`` for an empty/absent path.
    """
    if not path:
        return None
    try:
        p = Path(str(path))
        if p.is_absolute():
            return os.path.relpath(p, root)
        return str(p)
    except (ValueError, OSError):
        return str(path)


def _to_int(value: Any) -> Optional[int]:
    """Coerce a line number to int, tolerating strings/None/garbage."""
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Subprocess seam — the single place a scanner is launched. Tests patch this.
# --------------------------------------------------------------------------- #
def _scan_timeout(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("scan_timeout", _DEFAULT_SCAN_TIMEOUT)))
    except (TypeError, ValueError):
        return _DEFAULT_SCAN_TIMEOUT


def _invoke_scanner(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run a scanner subprocess and return ``(returncode, stdout, stderr)``.

    Fixed arg list, ``shell=False``; cwd is the repo root and PYTHONPATH is
    prefixed with it so the scanner's ``from tools...`` imports resolve when it is
    launched as a script or via ``python -c``. A timeout/missing-binary failure is
    surfaced as a non-zero return code with the reason on stderr — never raised —
    so one broken scanner degrades to "no findings", not a crashed assessment.

    This is the monkeypatch seam: tests replace it with a stub that returns canned
    scanner JSON, so normalization/persistence is tested without the real tools.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(  # nosec B603 — fixed arg list, shell=False, no shell metachars
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            env=env,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"scanner executable not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", f"scanner timed out after {timeout}s"


def _parse_json(stdout: str) -> Optional[dict]:
    """Parse a scanner's JSON stdout; tolerate leading/trailing noise lines.

    Scanners print a single JSON document on stdout under ``--json``/``-c``. If a
    stray banner sneaks in, fall back to the first ``{``..last ``}`` slice.
    """
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        start, end = stdout.find("{"), stdout.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(stdout[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


# --------------------------------------------------------------------------- #
# Normalization — each scanner's findings -> integrity_findings tuples
# --------------------------------------------------------------------------- #
def _normalize_sast(payload: dict, root: Path) -> list[dict]:
    """``run_sast`` output -> normalized findings (finding_type='dangerous_api').

    ``run_sast`` returns ``{"all_findings": [...]}`` aggregated across languages;
    each finding carries ``file``, ``line``, ``severity`` (UPPER), and a message
    (``issue_text`` for bandit, ``message`` for the others).
    """
    out = []
    for f in payload.get("all_findings", []) or []:
        detail = {
            "rule": f.get("test_id") or f.get("cwe") or "",
            "name": f.get("test_name", ""),
            "message": f.get("issue_text") or f.get("message", ""),
            "confidence": f.get("confidence", ""),
            "cwe": f.get("issue_cwe") or f.get("cwe", ""),
        }
        out.append(
            {
                "source_scanner": "sast",
                "finding_type": "dangerous_api",
                "severity": _norm_severity(f.get("severity"), default="low"),
                "file_path": _rel(f.get("file"), root),
                "line": _to_int(f.get("line")),
                "detail": detail,
            }
        )
    return out


def _normalize_secrets(payload: dict, root: Path) -> list[dict]:
    """``secret_detector.scan`` output -> normalized findings (finding_type='secret').

    Both the detect-secrets path and the built-in pattern fallback expose
    ``{"findings": [{file, line, type, severity, ...}]}``; built-in entries carry
    a redacted ``match_preview`` (never the raw secret value).
    """
    out = []
    for f in payload.get("findings", []) or []:
        detail = {
            "type": f.get("type", ""),
            "match_preview": f.get("match_preview", ""),
            "hashed_secret": f.get("hashed_secret", ""),
            "is_verified": f.get("is_verified", False),
        }
        out.append(
            {
                "source_scanner": "secrets",
                "finding_type": "secret",
                # Secrets default to 'high' — an embedded credential is serious
                # even when a scanner omits a severity.
                "severity": _norm_severity(f.get("severity"), default="high"),
                "file_path": _rel(f.get("file"), root),
                "line": _to_int(f.get("line")),
                "detail": detail,
            }
        )
    return out


def _normalize_deps(payload: dict, root: Path) -> list[dict]:
    """``dependency_auditor`` CLI output -> normalized findings (finding_type='vuln_dependency').

    The CLI emits ``{"results": {lang: {"findings": [...]}}}``; each finding is a
    vulnerable package (``package``, ``version``, ``vulnerability_id``,
    ``description``/``title``, ``severity``). Dependencies have no source line, so
    ``line`` is null and ``file_path`` carries the package coordinate for triage.
    """
    out = []
    results = payload.get("results", {}) or {}
    # Accept both the combined CLI shape ({results:{lang:{findings}}}) and a bare
    # single-language shape ({findings:[...]}) for forward-compatibility.
    buckets = list(results.values()) if results else [payload]
    for bucket in buckets:
        for f in (bucket or {}).get("findings", []) or []:
            pkg = f.get("package", "")
            ver = f.get("version", "")
            coord = f"{pkg}=={ver}" if pkg and ver else (pkg or None)
            detail = {
                "package": pkg,
                "version": ver,
                "vulnerability_id": f.get("vulnerability_id") or f.get("id", ""),
                "aliases": f.get("aliases", []),
                "description": f.get("description") or f.get("title", ""),
                "fix_versions": f.get("fix_versions", []),
            }
            out.append(
                {
                    "source_scanner": "deps",
                    "finding_type": "vuln_dependency",
                    "severity": _norm_severity(f.get("severity"), default="info"),
                    "file_path": coord,
                    "line": None,
                    "detail": detail,
                }
            )
    return out


# Formal-verifier checks we fold into findings — the property checks that flag a
# concrete security defect. Advisory/metric checks (cui_marking_presence,
# invariant_detection, property_suggestions) are intentionally excluded: they are
# not "unauthorized/malicious code" signals and would just be noise.
_FORMAL_FINDING_CHECKS = {
    "sql_injection_immunity",
    "dangerous_patterns",
    "input_validation",
}


def _normalize_formal(payload: dict, root: Path) -> list[dict]:
    """``formal_verifier`` output -> normalized findings (finding_type='dangerous_api').

    Accepts both the project sweep shape ``{"file_results": [{file, check_results}]}``
    and a single-file shape ``{file, check_results}``. Only the property checks in
    :data:`_FORMAL_FINDING_CHECKS` contribute findings; each contained finding maps
    to one ``integrity_findings`` row with ``source_scanner='formal'``. Severity
    comes from the per-finding severity (``warning``->``medium`` via the alias
    table), falling back to the check's severity.
    """
    out = []
    file_results = payload.get("file_results")
    if file_results is None:
        # single-file shape — treat the payload itself as one file result.
        file_results = [payload] if payload.get("check_results") else []
    for fr in file_results or []:
        fpath = fr.get("file") or fr.get("file_path")
        for chk in fr.get("check_results", []) or []:
            name = chk.get("check_name", "")
            if name not in _FORMAL_FINDING_CHECKS:
                continue
            for f in chk.get("findings", []) or []:
                detail = {
                    "check": name,
                    "category": chk.get("check_category", ""),
                    "description": f.get("description", ""),
                    "text": f.get("text", ""),
                    "function": f.get("function", ""),
                }
                out.append(
                    {
                        "source_scanner": "formal",
                        "finding_type": "dangerous_api",
                        "severity": _norm_severity(
                            f.get("severity") or chk.get("severity"), default="medium"
                        ),
                        "file_path": _rel(fpath, root),
                        "line": _to_int(f.get("line")),
                        "detail": detail,
                    }
                )
    return out


def _normalize_container(payload: dict, root: Path) -> list[dict]:
    """``container_scanner`` CLI output -> normalized findings.

    The CLI emits ``{"dockerfile_scan": {findings:[...]}, "image_scan": {findings:[...]}}``
    (either key may be absent depending on what was scanned):

      * Dockerfile static findings (running-as-root, ADD-vs-COPY, …) ->
        ``finding_type='dangerous_api'``; the ``DS007`` secrets-in-ENV check maps to
        ``finding_type='secret'`` since it is a credential exposure.
      * trivy image vulnerabilities -> ``finding_type='vuln_dependency'`` with the
        package coordinate carried in ``file_path`` (no source line for a CVE).

    Both land as ``source_scanner='container'``.
    """
    out = []

    df = payload.get("dockerfile_scan") or {}
    df_file = df.get("file")
    for f in df.get("findings", []) or []:
        ftype = "secret" if f.get("check_id") == "DS007" else "dangerous_api"
        detail = {
            "check_id": f.get("check_id", ""),
            "name": f.get("name", ""),
            "description": f.get("description", ""),
            "line_content": f.get("line_content", ""),
        }
        out.append(
            {
                "source_scanner": "container",
                "finding_type": ftype,
                "severity": _norm_severity(f.get("severity"), default="low"),
                "file_path": _rel(df_file, root),
                "line": _to_int(f.get("line")),
                "detail": detail,
            }
        )

    img = payload.get("image_scan") or {}
    for f in img.get("findings", []) or []:
        pkg = f.get("package", "")
        ver = f.get("installed_version", "")
        coord = f"{pkg}=={ver}" if pkg and ver else (pkg or _rel(f.get("target"), root))
        detail = {
            "vulnerability_id": f.get("vulnerability_id", ""),
            "package": pkg,
            "installed_version": ver,
            "fixed_version": f.get("fixed_version", ""),
            "title": f.get("title", ""),
            "primary_url": f.get("primary_url", ""),
            "cvss_score": f.get("cvss_score"),
        }
        out.append(
            {
                "source_scanner": "container",
                "finding_type": "vuln_dependency",
                "severity": _norm_severity(f.get("severity"), default="info"),
                "file_path": coord,
                "line": None,
                "detail": detail,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _persist(conn: Any, assessment_id: int, findings: list[dict]) -> list[int]:
    """Append every normalized finding to ``integrity_findings``; return new ids."""
    tenant_id, classification, _ = _caller_context()
    ids = []
    for f in findings:
        fid = _insert_finding(
            conn,
            (
                assessment_id,
                f["source_scanner"],
                f["finding_type"],
                f["severity"],
                f["file_path"],
                f["line"],
                json.dumps(f["detail"]),
                tenant_id,
                classification,
            ),
        )
        ids.append(fid)
    return ids


def _run_adapter(
    scanner: str,
    cmd: list[str],
    normalize,
    assessment_id: int,
    staged: Path,
    conn: Any,
) -> dict:
    """Shared adapter body: invoke -> parse -> normalize -> persist -> summarize."""
    cfg = _load_config()
    rc, stdout, stderr = _invoke_scanner(cmd, _scan_timeout(cfg))
    payload = _parse_json(stdout)

    if payload is None:
        logger.warning(
            "%s scanner produced no parseable output for assessment %s (rc=%s): %s",
            scanner, assessment_id, rc, (stderr or "").strip()[:200],
        )
        return {
            "scanner": scanner,
            "success": False,
            "findings_persisted": 0,
            "finding_ids": [],
            "error": (stderr or "no scanner output").strip()[:500],
        }

    normalized = normalize(payload, staged)
    finding_ids = _persist(conn, assessment_id, normalized)
    return {
        "scanner": scanner,
        "success": True,
        "findings_persisted": len(finding_ids),
        "finding_ids": finding_ids,
        "error": None,
    }


# --------------------------------------------------------------------------- #
# Public API — one adapter per scanner + a fan-out
# --------------------------------------------------------------------------- #
def run_sast_scan(assessment_id: int, staged_path: Optional[str] = None, conn: Any = None) -> dict:
    """Shell out to ``sast_runner.run_sast`` over the quarantined tree.

    Multi-language: ``run_sast`` auto-detects languages and dispatches the right
    SAST tool (bandit / gosec / spotbugs / clippy / eslint-security / SCS).
    Findings land as ``source_scanner='sast'`` / ``finding_type='dangerous_api'``.
    """
    staged = _staged_dir(assessment_id, staged_path)
    # `python -c` so we reach the multi-language run_sast() (the module CLI only
    # exposes single-language bandit). Fixed arg list; the staged path is argv[1],
    # never interpolated into the snippet, so there is nothing to inject.
    cmd = [
        sys.executable,
        "-c",
        "import json,sys;from tools.security.sast_runner import run_sast;"
        "print(json.dumps(run_sast(sys.argv[1])))",
        str(staged),
    ]
    return _with_conn(conn, lambda c: _run_adapter(
        "sast", cmd, _normalize_sast, assessment_id, staged, c
    ))


def run_secret_scan(assessment_id: int, staged_path: Optional[str] = None, conn: Any = None) -> dict:
    """Shell out to ``secret_detector.py`` over the quarantined tree.

    Uses detect-secrets when installed, else the deterministic built-in pattern
    scanner. Findings land as ``source_scanner='secrets'`` / ``finding_type='secret'``.
    """
    staged = _staged_dir(assessment_id, staged_path)
    cmd = [
        sys.executable,
        str(_SECRET_DETECTOR),
        "--project-path",
        str(staged),
        "--json",
    ]
    return _with_conn(conn, lambda c: _run_adapter(
        "secrets", cmd, _normalize_secrets, assessment_id, staged, c
    ))


def run_dependency_scan(assessment_id: int, staged_path: Optional[str] = None, conn: Any = None) -> dict:
    """Shell out to ``dependency_auditor.py`` (auto-detect) over the quarantined tree.

    Audits whatever dependency manifests are present (requirements.txt, package.json,
    go.mod, Cargo.toml, pom.xml, *.csproj). Findings land as ``source_scanner='deps'``
    / ``finding_type='vuln_dependency'``.
    """
    staged = _staged_dir(assessment_id, staged_path)
    cmd = [
        sys.executable,
        str(_DEPENDENCY_AUDITOR),
        "--project-path",
        str(staged),
        "--language",
        "auto",
        "--json",
    ]
    return _with_conn(conn, lambda c: _run_adapter(
        "deps", cmd, _normalize_deps, assessment_id, staged, c
    ))


def run_formal_scan(assessment_id: int, staged_path: Optional[str] = None, conn: Any = None) -> dict:
    """Shell out to ``formal_verifier.verify_project`` over the quarantined tree.

    Runs the property checks (SQL-injection immunity, dangerous patterns, input
    validation) across every ``.py`` file in the staged tree and folds the flagged
    defects into ``integrity_findings`` as ``source_scanner='formal'`` /
    ``finding_type='dangerous_api'``. Opt-in (``scanners.formal`` defaults off) — it
    is slower than the regex scanners. Disabled scanners are skipped, never failed.
    """
    staged = _staged_dir(assessment_id, staged_path)
    # `python -c` to reach the aggregated verify_project() sweep. Fixed arg list;
    # the staged path is argv[1], never interpolated into the snippet.
    cmd = [
        sys.executable,
        "-c",
        "import json,sys;from tools.analysis.formal_verifier import verify_project;"
        "print(json.dumps(verify_project(sys.argv[1]), default=str))",
        str(staged),
    ]
    return _with_conn(conn, lambda c: _run_adapter(
        "formal", cmd, _normalize_formal, assessment_id, staged, c
    ))


def _find_dockerfiles(staged: Path) -> list[Path]:
    """Locate Dockerfiles in the quarantined tree (``Dockerfile``, ``Dockerfile.*``,
    ``*.Dockerfile``; case-insensitive). Returns ``[]`` when the tree is absent or
    holds no container recipe — the signal the container adapter no-ops on."""
    found: list[Path] = []
    if not staged.exists():
        return found
    excludes = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp"}
    for root, dirs, files in os.walk(staged):
        dirs[:] = [d for d in dirs if d not in excludes]
        for fn in files:
            low = fn.lower()
            if low == "dockerfile" or low.startswith("dockerfile.") or low.endswith(".dockerfile"):
                found.append(Path(root) / fn)
    return found


def run_container_scan(
    assessment_id: int,
    staged_path: Optional[str] = None,
    conn: Any = None,
    image: Optional[str] = None,
) -> dict:
    """Conditionally scan container artifacts in the quarantined tree.

    The container scan runs **only when a Dockerfile is present** in the staged
    tree (or an explicit ``image`` reference is supplied). When neither exists this
    is a clean **no-op** — ``success=True``, zero findings, ``skipped=True`` — never
    a failure: most quarantined sources are not containerized. When a Dockerfile is
    present, ``container_scanner.py`` analyzes it (and trivy scans ``image`` if
    given); findings land as ``source_scanner='container'``. Opt-in
    (``scanners.container`` defaults off; requires trivy for image scans).
    """
    staged = _staged_dir(assessment_id, staged_path)
    dockerfiles = _find_dockerfiles(staged)

    if not dockerfiles and not image:
        return {
            "scanner": "container",
            "success": True,
            "findings_persisted": 0,
            "finding_ids": [],
            "error": None,
            "skipped": True,
            "reason": "no Dockerfile or image in quarantined tree",
        }

    def _body(c: Any) -> dict:
        timeout = _scan_timeout(_load_config())
        targets: list[list[str]] = [["--dockerfile", str(p)] for p in dockerfiles]
        if image:
            targets.append(["--image", image])

        normalized: list[dict] = []
        errors: list[str] = []
        for target in targets:
            cmd = [sys.executable, str(_CONTAINER_SCANNER), *target, "--json"]
            rc, stdout, stderr = _invoke_scanner(cmd, timeout)
            payload = _parse_json(stdout)
            if payload is None:
                errors.append(f"{target[0]} {target[1]}: " + (stderr or "no output").strip()[:160])
                continue
            normalized.extend(_normalize_container(payload, staged))

        finding_ids = _persist(c, assessment_id, normalized)
        return {
            "scanner": "container",
            "success": not errors,
            "findings_persisted": len(finding_ids),
            "finding_ids": finding_ids,
            "error": "; ".join(errors)[:500] or None,
            "dockerfiles_scanned": len(dockerfiles),
        }

    return _with_conn(conn, _body)


# Map a scanner key (matches integrity_config.yaml ``scanners`` toggles and
# constants.FINDING_SCANNERS) to its adapter.
_ADAPTERS = {
    "sast": run_sast_scan,
    "secrets": run_secret_scan,
    "deps": run_dependency_scan,
    "formal": run_formal_scan,
    "container": run_container_scan,
}

# Default participation in ``scan_all`` when a toggle is absent from config. The
# regex/SCA scanners default on; the heavier opt-in scanners (formal, container)
# default off so they only run when explicitly enabled.
_DEFAULT_TOGGLES = {
    "sast": True,
    "secrets": True,
    "deps": True,
    "formal": False,
    "container": False,
}


def scan_all(assessment_id: int, staged_path: Optional[str] = None, conn: Any = None) -> dict:
    """Run every enabled scanner adapter and aggregate the results.

    Honors the ``scanners`` toggles in ``args/integrity_config.yaml`` (sast /
    secrets / deps default on). Each adapter runs in its own subprocess; a single
    scanner failure is recorded but never aborts the others.

    Returns ``{"assessment_id", "scanners": {name: <adapter result>},
    "total_findings": int, "finding_ids": [...]}``.
    """
    cfg = _load_config()
    toggles = cfg.get("scanners", {}) or {}

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        per_scanner: dict[str, dict] = {}
        all_ids: list[int] = []
        for name, adapter in _ADAPTERS.items():
            default = _DEFAULT_TOGGLES.get(name, True)
            enabled = toggles.get(name, default)
            if not enabled:
                # A default-on scanner that is explicitly disabled is recorded as
                # skipped (visibility). An opt-in scanner (default off) that was not
                # turned on is omitted entirely so it doesn't clutter the report.
                if default:
                    per_scanner[name] = {
                        "scanner": name,
                        "success": True,
                        "findings_persisted": 0,
                        "finding_ids": [],
                        "error": None,
                        "skipped": True,
                    }
                continue
            result = adapter(assessment_id, staged_path=staged_path, conn=conn)
            per_scanner[name] = result
            all_ids.extend(result.get("finding_ids", []))
    finally:
        if own_conn:
            conn.close()

    return {
        "assessment_id": assessment_id,
        "scanners": per_scanner,
        "total_findings": len(all_ids),
        "finding_ids": all_ids,
    }


def _with_conn(conn: Any, fn):
    """Run ``fn(conn)`` opening/closing an RLS-aware connection when none given.

    ``init_db`` is invoked once on the (possibly fresh) connection so a single
    adapter can be called standalone before any assessment row's tables exist.
    """
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent
        return fn(conn)
    finally:
        if own_conn:
            conn.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SIPA scanner adapters — SAST / secrets / deps -> integrity_findings"
    )
    parser.add_argument("--assessment-id", type=int, required=True,
                        help="integrity_assessments.id to attach findings to")
    parser.add_argument("--staged-path", help="override quarantined tree path")
    parser.add_argument("--scanner", choices=sorted(_ADAPTERS), default=None,
                        help="run a single scanner (default: all enabled)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    if args.scanner:
        result = _ADAPTERS[args.scanner](args.assessment_id, staged_path=args.staged_path)
    else:
        result = scan_all(args.assessment_id, staged_path=args.staged_path)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if "scanners" in result:
            print(f"SIPA scan — assessment {result['assessment_id']}")
            for name, r in result["scanners"].items():
                state = "skipped" if r.get("skipped") else ("ok" if r["success"] else "FAIL")
                print(f"  [{state}] {name}: {r['findings_persisted']} finding(s)"
                      + (f" — {r['error']}" if r.get("error") else ""))
            print(f"  total: {result['total_findings']} finding(s) persisted")
        else:
            state = "ok" if result["success"] else "FAIL"
            print(f"[{state}] {result['scanner']}: {result['findings_persisted']} finding(s)"
                  + (f" — {result['error']}" if result.get("error") else ""))


if __name__ == "__main__":
    main()
