# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Pre-merge / PR gate (eqo-sipa-01).

``assess_changed_files(base='origin/main', ...)`` runs a SIPA assessment over **only
the Python files a branch changed** (``git diff`` against ``base``), so a code-quality
/ pre-merge gate can block a PR that introduces a known-bad signature or a high-risk
behavioral footprint — without re-scanning the whole repository every time.

It is a thin orchestration over the existing SIPA engine building blocks — it does
NOT reimplement scanning:

  1. ``git diff --name-only <base>...HEAD`` (or ``--cached``) -> changed ``*.py``
     paths that still exist on disk.
  2. Assemble just those files into a throwaay source tree, **preserving their
     repo-relative paths** (with parent dirs) so intra-package imports still resolve
     for the AST capability extractor, then hand that subset to
     :func:`tools.integrity.ingest.stage` — which quarantines it under
     ``.tmp/integrity_quarantine/<assessment_id>/`` exactly like any other artifact.
  3. ``scanners.scan_all`` (SAST / secrets / deps / malicious-signature) +
     ``capability_extractor.extract_and_persist`` (AST behavioral manifest) +
     ``scoring.score`` over the staged subset — the same stages and thresholds the
     full ``engine.assess`` pipeline uses.
  4. Return the verdict (``allow`` / ``review`` / ``quarantine``) + risk score +
     findings; the CLI maps the verdict to an exit code via
     :func:`tools.integrity.engine.gate_exit_code` (exit 1 on a blocking verdict).

SECURITY INVARIANTS (inherited from the engine stages — the SIPA static-only contract):
  * **Never executes the changed code.** The subset is *copied* into quarantine and
    analyzed statically (isolated scanner subprocesses + AST parsing). The only
    subprocess here is a fixed-arg, ``shell=False`` ``git diff`` (read-only); nothing
    imports, ``exec``-es, or runs the changed files. ``shell=True`` is banned across
    ``tools/integrity/`` (enforced by ``test_integrity_no_shell_exec.py``).
  * **All DB access via the threaded RLS-aware connection** (or one opened internally).

The feature flag ``ICDEV_INTEGRITY_ENABLED`` (``engine.is_enabled``) gates the *CLI /
automatic* invocation (a disabled canvas no-ops to a pass); ``assess_changed_files``
itself is the explicit primitive and runs whenever a caller invokes it directly,
mirroring ``engine.assess``.
"""
from __future__ import annotations

import os
import shutil
# subprocess is used ONLY for a fixed-arg, shell=False ``git diff`` (read-only);
# the changed files are never executed. shell=True is banned under tools/integrity/.
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any, Optional

from tools.integrity import capability_extractor, engine, ingest, scanners, scoring
from tools.integrity.db.init_db import init_db

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.pr_gates")

BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/pr_gates.py -> repo root


# --------------------------------------------------------------------------- #
# Changed-file discovery (git diff — fixed-arg, shell=False, read-only)
# --------------------------------------------------------------------------- #
def changed_py_files(
    base: str = "origin/main",
    cached: bool = False,
    repo_root: Optional[Path] = None,
) -> list[str]:
    """Return the repo-relative ``*.py`` paths a branch changed, that still exist.

    Runs ``git diff --name-only <base>...HEAD`` (three-dot: changes on HEAD since the
    merge-base with ``base``) or, when ``cached`` is set, ``git diff --cached
    --name-only`` (the staged index). The result is filtered to ``.py`` files that are
    still present on disk — a deleted/renamed-away file cannot be staged for scanning.

    Args:
        base: the ref to diff against (default ``origin/main``); ignored when ``cached``.
        cached: diff the staged index instead of ``base...HEAD``.
        repo_root: the git working tree to run in (default: the ICDEV repo root). Tests
            point this at a throwaway repo.

    Returns:
        Sorted list of repo-relative POSIX-style paths (e.g. ``tools/foo/bar.py``).
        Empty when nothing Python changed.
    """
    root = Path(repo_root) if repo_root else BASE_DIR
    git = shutil.which("git") or "git"
    if cached:
        args = [git, "diff", "--cached", "--name-only"]
    else:
        # Three-dot: only the changes HEAD introduced since branching off base.
        args = [git, "diff", "--name-only", f"{base}...HEAD"]

    try:
        # Fixed arg list, shell=False, read-only diff — safe by construction.
        proc = subprocess.run(  # nosec B603
            args,
            cwd=str(root),
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("changed_py_files: git executable not found on PATH")
        return []

    if proc.returncode != 0:
        logger.warning(
            "changed_py_files: git diff failed (exit %s): %s",
            proc.returncode, (proc.stderr or "").strip(),
        )
        return []

    files: list[str] = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel or not rel.endswith(".py"):
            continue
        if (root / rel).is_file():
            files.append(rel.replace("\\", "/"))
    return sorted(set(files))


def _assemble_subset(files: list[str], repo_root: Path, dest_root: Path) -> int:
    """Copy each changed file into ``dest_root`` preserving its repo-relative path.

    Preserving the relative layout (with parent dirs) keeps intra-package import
    structure intact so the AST capability extractor resolves the subset the same way
    it sits in the tree. Returns the number of files actually copied.
    """
    copied = 0
    for rel in files:
        src = repo_root / rel
        if not src.is_file():
            continue
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    return copied


# --------------------------------------------------------------------------- #
# Findings read-back
# --------------------------------------------------------------------------- #
def _fetch_findings(conn: Any, assessment_id: int) -> list[dict]:
    """Return the append-only ``integrity_findings`` rows for an assessment as dicts."""
    sql = (
        "SELECT source_scanner, finding_type, severity, file_path, line, detail "
        "FROM integrity_findings WHERE assessment_id = ? ORDER BY id"
    )
    sql = engine._q(conn, sql)
    rows = conn.execute(sql, (assessment_id,)).fetchall()
    cols = ("source_scanner", "finding_type", "severity", "file_path", "line", "detail")
    out: list[dict] = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({c: row[c] for c in cols})
        else:
            out.append(dict(zip(cols, row)))
    return out


def _empty_result(verdict: str = "allow") -> dict:
    """The no-op disposition for an empty change set (nothing Python to assess)."""
    return {
        "verdict": verdict,
        "risk_score": 0.0,
        "findings": [],
        "files_assessed": [],
        "assessment_id": None,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def assess_changed_files(
    base: str = "origin/main",
    mode: str = "auto",
    cached: bool = False,
    repo_root: Optional[Path] = None,
    conn: Any = None,
) -> dict:
    """Run a SIPA assessment over only the ``*.py`` files a branch changed.

    Discovers the changed Python files (see :func:`changed_py_files`), stages just
    that subset into quarantine via :func:`ingest.stage`, then runs the standard
    SIPA stages (``scan_all`` -> ``extract_and_persist`` -> ``score``) over the staged
    copy. Reuses the engine's verdict thresholds verbatim — no scanning is
    reimplemented here.

    Args:
        base: git ref to diff ``HEAD`` against (default ``origin/main``).
        mode: assessment mode (``auto`` / ``provenance_aware`` / ``provenance_blind``);
            validated + stamped onto the assessment row. PR gating is behavior-only
            (no claim/RTM reconciliation), so the verdict is driven by scanner +
            signature + capability signals.
        cached: diff the staged index (``git diff --cached``) instead of ``base...HEAD``.
        repo_root: git working tree to scan (default: the ICDEV repo root).
        conn: optional existing RLS-aware connection to reuse (engine / tests); when
            ``None`` one is opened and closed internally.

    Returns:
        ``{"verdict", "risk_score", "findings", "files_assessed", "assessment_id"}``.
        ``verdict`` is the canonical lowercase token (``constants.VERDICTS``). An empty
        change set returns the no-op ``allow`` disposition with ``assessment_id=None``.

    Raises:
        ValueError: an invalid ``mode`` (via :func:`engine.resolve_mode`).

    Never executes the changed code — staging copies bytes and every stage is
    static-only (isolated scanner subprocesses, AST parsing).
    """
    resolved_mode = engine.resolve_mode(mode, None, None)
    root = Path(repo_root) if repo_root else BASE_DIR

    files = changed_py_files(base, cached=cached, repo_root=root)
    if not files:
        logger.info("assess_changed_files: no changed .py files vs %s — pass", base)
        return _empty_result()

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()

    # Throwaway source tree holding the changed subset (copied into quarantine by
    # ingest.stage; removed once staged so it does not linger).
    subset_dir = Path(tempfile.mkdtemp(prefix="sipa_pr_"))
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS

        copied = _assemble_subset(files, root, subset_dir)
        if copied == 0:
            logger.info("assess_changed_files: changed files vanished before staging — pass")
            return _empty_result()

        # 1. Stage the subset into quarantine (records the integrity_assessments row).
        staged = ingest.stage(str(subset_dir), conn=conn)
        assessment_id = staged["assessment_id"]
        staged_path = staged["staged_path"]
        engine._set_mode(conn, assessment_id, resolved_mode)
        logger.info(
            "assess_changed_files: staged %d changed file(s) -> assessment %s (%s)",
            copied, assessment_id, resolved_mode,
        )

        # 2. Scanners + capability manifest + score (the engine stages, reused).
        scanners.scan_all(assessment_id, staged_path=staged_path, conn=conn)
        capability_extractor.extract_and_persist(assessment_id, staged_path, conn=conn)
        score_result = scoring.score(assessment_id, conn=conn)

        findings = _fetch_findings(conn, assessment_id)
    finally:
        shutil.rmtree(subset_dir, ignore_errors=True)
        if own_conn:
            conn.close()

    logger.info(
        "assess_changed_files: assessment %s -> %s (%.1f) | %d file(s), %d finding(s)",
        assessment_id, score_result["verdict"], score_result["risk_score"],
        len(files), len(findings),
    )
    return {
        "verdict": score_result["verdict"],
        "risk_score": score_result["risk_score"],
        "findings": findings,
        "files_assessed": files,
        "assessment_id": assessment_id,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None, conn: Any = None) -> int:
    """SIPA PR-gate CLI entrypoint.

    ``python tools/integrity/pr_gates.py --base origin/main [--cached]
    [--mode auto|provenance_aware|provenance_blind] [--gate] [--json]``

    ``--gate`` maps the verdict to a CI / pre-merge exit code via
    :func:`engine.gate_exit_code` (non-zero on ``gate.block_on`` — QUARANTINE by
    default — zero otherwise) so it can fail a build. Honors
    ``ICDEV_INTEGRITY_ENABLED``: when the canvas is disabled the gate no-ops to a
    pass (exit 0) rather than scanning.

    Returns:
        The process exit code (also raised via ``SystemExit``): the gate exit code
        when ``--gate``, else ``0``.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="pr_gates.py",
        description="SIPA pre-merge gate — assess only the Python files a branch "
        "changed (git diff vs a base ref) and emit a risk score + ALLOW / REVIEW / "
        "QUARANTINE verdict. Static-only: the changed code is never executed.",
    )
    parser.add_argument("--base", default="origin/main", help="git ref to diff HEAD against")
    parser.add_argument(
        "--cached",
        action="store_true",
        help="diff the staged index (git diff --cached) instead of base...HEAD",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", engine.PROVENANCE_AWARE, engine.PROVENANCE_BLIND],
        help="assessment mode (default: auto)",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero on a blocking verdict (gate.block_on; QUARANTINE by default)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    # Honor the feature flag for the automatic / CLI invocation: a disabled canvas
    # no-ops to a pass rather than scanning.
    if not engine.is_enabled():
        result = _empty_result()
        result["skipped"] = "ICDEV_INTEGRITY_ENABLED is not set"
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("SIPA PR gate skipped — ICDEV_INTEGRITY_ENABLED is not set")
        sys.exit(engine.GATE_OK)

    result = assess_changed_files(
        base=args.base,
        mode=args.mode,
        cached=args.cached,
        conn=conn,
    )

    verdict = str(result["verdict"]).lower()
    block_set, warn_set = engine._gate_verdicts()
    code = engine.gate_exit_code(verdict) if args.gate else engine.GATE_OK

    if args.json:
        payload = dict(result)
        if args.gate:
            payload["gate"] = {
                "blocked": verdict in block_set,
                "warned": verdict in warn_set,
                "exit_code": code,
            }
        print(json.dumps(payload, indent=2))
    else:
        print(f"SIPA PR gate — {len(result['files_assessed'])} changed .py file(s)")
        print(f"  verdict:    {verdict.upper()}")
        print(f"  risk_score: {result['risk_score']}")
        print(f"  findings:   {len(result['findings'])}")
        if result.get("assessment_id") is not None:
            print(f"  assessment: {result['assessment_id']}")

    if args.gate:
        if verdict in block_set:
            print(f"GATE: BLOCKED - verdict {verdict.upper()} is blocking", file=sys.stderr)
        elif verdict in warn_set:
            print(f"GATE: WARN - verdict {verdict.upper()} (non-blocking)", file=sys.stderr)

    sys.exit(code)


if __name__ == "__main__":
    main()
