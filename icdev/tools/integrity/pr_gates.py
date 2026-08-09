# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — PR diff gate.

``assess_changed_files(base, mode, conn)`` runs a SIPA assessment over **only the
Python files changed on a branch** (the ``git diff`` against a base ref), so a
pre-merge / CI hook can block a pull request that introduces unauthorized or
malicious code without re-scanning the whole tree on every push.

Pipeline (a thin orchestration over the existing SIPA building blocks — nothing
here re-implements scanning):

  1. ``git diff --name-only <base>...HEAD`` (or ``--cached`` for the staged index)
     and keep the changed ``*.py`` paths plus any changed dependency manifest
     (requirements.txt, package.json, go.mod, …) that still exists on disk (a
     deleted file cannot be assessed). Staging the changed manifest — and only the
     changed manifest — keeps the deps / SCA scanner scoped to the PR instead of
     auditing the ambient repo environment.
  2. Copy just those files into a throw-away subtree under the quarantine root,
     **preserving their repo-relative paths** so intra-package imports still
     resolve and every finding's ``file_path`` maps back to the real PR path.
  3. ``ingest.stage`` the subtree (quarantine-first staging + assessment row) ->
     ``scanners.scan_all`` -> ``capability_extractor.extract_and_persist`` ->
     ``scoring.score`` over that staged subset.
  4. Return ``{verdict, risk_score, findings, files_assessed}``; the CLI maps the
     verdict to an exit code via the shared ``engine.gate_exit_code`` (exit 1 on a
     blocking verdict — QUARANTINE by default — exit 0 otherwise).

SECURITY INVARIANTS (inherited from every reused stage — the SIPA static-only
contract): the changed files are only *copied* into quarantine and analyzed
statically (isolated scanner subprocesses + AST parsing). Nothing here imports,
``exec``-es, or runs the changed code. The single ``subprocess`` call is a
fixed-arg, ``shell=False`` ``git diff`` that reads file *names* only.

The feature flag ``ICDEV_INTEGRITY_ENABLED`` gates the *CLI* (so a CI hook no-ops
to a pass when the canvas is toggled off); :func:`assess_changed_files` is the
explicit primitive and runs whenever a caller invokes it directly (mirrors
``engine.assess``).
"""
from __future__ import annotations

import shutil
# subprocess is used ONLY for a fixed-arg, shell=False ``git diff --name-only``
# (file names only); the changed code is never executed.
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any, Optional

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.integrity import (
    capability_extractor,
    engine,
    ingest,
    scanners,
    scoring,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.pr_gates")

BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/pr_gates.py -> repo root

# Default base ref the branch is diffed against (the integration target).
DEFAULT_BASE = "origin/main"

# Fixed timeout for the name-only diff; a hung git is killed, not left running.
_DIFF_TIMEOUT = 60

# Dependency manifests we stage alongside the changed *.py files so the deps /
# SCA scanner stays scoped to the *changed subset*: a PR that bumps a dependency
# has its manifest assessed, but a benign code-only change stages no manifest and
# the deps scanner finds nothing (rather than auditing the ambient repo
# environment). Basenames are matched case-sensitively; ``*.csproj`` by suffix.
_DEP_MANIFESTS = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "package-lock.json",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}


def _is_dep_manifest(name: str) -> bool:
    """True when ``name`` (a repo-relative path) is a dependency manifest.

    Matches the known manifest basenames plus ``*.csproj`` (C# project files);
    ``setup.py`` is intentionally included even though it ends in ``.py`` so the
    intent is explicit (it is staged either way).
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base in _DEP_MANIFESTS or base.endswith(".csproj")


class GitDiffError(RuntimeError):
    """Raised when the ``git diff`` that drives the gate cannot be computed."""


# --------------------------------------------------------------------------- #
# Changed-file discovery
# --------------------------------------------------------------------------- #
def _git_changed_files(base: str, cached: bool, repo_root: Path) -> list[str]:
    """Repo-relative paths changed on the branch, via a fixed-arg ``git diff``.

    ``--cached`` diffs the staged index against HEAD (``git diff --cached``);
    otherwise the branch is diffed against ``base`` with the three-dot
    ``<base>...HEAD`` form (changes since the merge-base, i.e. what the PR adds).
    ``shell=False`` and a fixed arg list — the refs are never interpolated into a
    shell string.
    """
    git = shutil.which("git") or "git"
    if cached:
        args = [git, "diff", "--cached", "--name-only"]
    else:
        args = [git, "diff", "--name-only", f"{base}...HEAD"]

    try:
        proc = subprocess.run(  # nosec B603 — fixed arg list, shell=False, names only
            args,
            cwd=str(repo_root),
            shell=False,
            capture_output=True,
            text=True,
            timeout=_DIFF_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:  # git binary absent
        raise GitDiffError("git executable not found on PATH; cannot diff") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitDiffError(f"git diff timed out after {_DIFF_TIMEOUT}s") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitDiffError(f"git diff failed (exit {proc.returncode}): {detail}")

    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _changed_assessable_files(base: str, cached: bool, repo_root: Path) -> list[str]:
    """The changed ``*.py`` paths + dependency manifests that still exist on disk.

    The assessable subset is the changed Python files (for the SAST / secret /
    signature scanners) plus any changed dependency manifest (for the deps / SCA
    scanner) — so the deps scan stays scoped to what the PR actually touched
    instead of auditing the ambient repo environment. Deleted / renamed-away files
    are dropped — there is nothing on disk to stage and assess. Paths are returned
    repo-relative (git's native ``--name-only`` form), with forward slashes
    preserved so they round-trip back to PR paths.
    """
    changed = _git_changed_files(base, cached, repo_root)
    return [
        name
        for name in changed
        if (name.endswith(".py") or _is_dep_manifest(name))
        and (repo_root / name).is_file()
    ]


# --------------------------------------------------------------------------- #
# Staging — copy just the changed files into a path-preserving subtree
# --------------------------------------------------------------------------- #
def _build_subset_tree(py_files: list[str], repo_root: Path) -> tuple[Path, list[str]]:
    """Copy the changed files into a temp subtree under the quarantine root.

    The subtree mirrors each file's repo-relative path (parent dirs created) so
    intra-package imports still resolve and every finding's ``file_path`` maps
    back to the PR path. Lives under the SIPA quarantine root (honoring the
    ``ICDEV_INTEGRITY_QUARANTINE_DIR`` override) so it shares the canvas's
    disposable scratch area. Returns ``(subtree_dir, staged_rel_paths)``.
    """
    quarantine_base = ingest._quarantine_base(ingest._load_config())
    quarantine_base.mkdir(parents=True, exist_ok=True)
    subtree = Path(tempfile.mkdtemp(prefix="pr_subset_", dir=str(quarantine_base)))

    staged_rel: list[str] = []
    for rel in py_files:
        src = repo_root / rel
        if not src.is_file():
            continue
        dest = subtree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        staged_rel.append(rel)
    return subtree, staged_rel


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def assess_changed_files(
    base: str = DEFAULT_BASE,
    mode: str = "auto",
    conn: Any = None,
    cached: bool = False,
    repo_root: Any = None,
) -> dict:
    """Run a SIPA assessment over the Python files changed on the branch.

    Diffs the branch against ``base`` (or the staged index when ``cached``), keeps
    the changed ``*.py`` files and any changed dependency manifest, stages just
    those into a path-preserving subtree, and runs the existing SIPA pipeline
    (ingest -> scan_all -> capability extract -> score) over the subset.

    Args:
        base: base ref to diff against (default ``origin/main``); ignored when
            ``cached`` is set.
        mode: ``'auto'`` (default), ``'provenance_aware'``, or
            ``'provenance_blind'`` — resolved via :func:`engine.resolve_mode` and
            stamped onto the assessment row. With no provenance handle ``auto``
            resolves to ``provenance_blind`` (the diff-gate default).
        conn: optional existing RLS-aware connection to reuse (CLI / tests); when
            ``None`` one is opened and closed internally.
        cached: diff the staged index (``git diff --cached``) instead of
            ``<base>...HEAD`` — for a pre-commit hook.
        repo_root: repository root to run ``git diff`` in (default: the ICDEV repo
            root containing this module).

    Returns:
        ``{"verdict", "risk_score", "findings", "files_assessed", "assessment_id",
        "mode"}``. With no changed ``*.py`` files or dependency manifests this
        short-circuits to an ALLOW with an empty ``findings`` / ``files_assessed``
        and ``assessment_id=None`` (nothing is staged or written).

    Never executes the changed code — every stage is static-only.
    """
    repo_root = Path(repo_root) if repo_root else BASE_DIR
    changed_files = _changed_assessable_files(base, cached, repo_root)

    if not changed_files:
        logger.info(
            "assess_changed_files: no changed *.py / dependency-manifest files "
            "(base=%s, cached=%s)", base, cached,
        )
        return {
            "assessment_id": None,
            "verdict": "allow",
            "risk_score": 0.0,
            "findings": [],
            "files_assessed": [],
            "mode": engine.resolve_mode(mode, None, None),
        }

    # PR gate carries no provenance handle, so 'auto' -> provenance_blind. We run
    # the static scanners + capability/score subset (no reconciliation stage), so
    # the resolved mode is recorded for provenance but does not branch the flow.
    resolved_mode = engine.resolve_mode(mode, None, None)

    subtree, staged_rel = _build_subset_tree(changed_files, repo_root)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        # 1. Ingest — quarantine-first staging of the changed-file subtree.
        staged = ingest.stage(str(subtree), conn=conn)
        assessment_id = staged["assessment_id"]
        staged_path = staged["staged_path"]
        engine._set_mode(conn, assessment_id, resolved_mode)
        logger.info(
            "assess_changed_files: staged %d changed file(s) -> assessment %s at %s",
            len(staged_rel), assessment_id, staged_path,
        )

        # 2. Scanners — SAST / secrets / deps / malicious-signature over the subset.
        scanners.scan_all(assessment_id, staged_path=staged_path, conn=conn)

        # 3. Capabilities — AST behavioral manifest of the subset.
        capability_extractor.extract_and_persist(assessment_id, staged_path, conn=conn)

        # 4. Scoring — combine the subset's signals into a risk score + verdict.
        score_result = scoring.score(assessment_id, conn=conn)

        # Findings (detail parsed) for the caller's report, reusing the scorer's reader.
        findings = scoring._fetch_findings(conn, assessment_id)
    finally:
        if own_conn:
            conn.close()
        # The path-preserving subtree was a copy into scratch; the authoritative
        # staged tree lives at <quarantine>/<assessment_id>/. Drop the subtree.
        shutil.rmtree(subtree, ignore_errors=True)

    logger.info(
        "assess_changed_files: assessment %s -> %s (%.1f) over %d file(s), %d finding(s)",
        assessment_id, score_result["verdict"], score_result["risk_score"],
        len(staged_rel), len(findings),
    )
    return {
        "assessment_id": assessment_id,
        "verdict": score_result["verdict"],
        "risk_score": score_result["risk_score"],
        "findings": findings,
        "files_assessed": staged_rel,
        "mode": resolved_mode,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None, conn: Any = None) -> int:
    """SIPA PR-gate CLI entrypoint.

    ``python tools/integrity/pr_gates.py --base origin/main [--cached]
    [--mode auto|provenance_aware|provenance_blind] [--repo-root .] [--gate]
    [--json]``

    ``--gate`` maps the verdict to a CI / pre-merge exit code via the shared
    ``engine.gate_exit_code`` (exit 1 on ``gate.block_on`` — QUARANTINE by default
    — exit 0 otherwise). Honors ``ICDEV_INTEGRITY_ENABLED``: when the canvas is
    toggled off the gate no-ops to a pass (exit 0) so a CI hook stays green.

    Args:
        argv: optional argument vector (defaults to ``sys.argv[1:]``); passed by
            tests to drive the CLI without touching the process args.
        conn: optional DB connection threaded into :func:`assess_changed_files`
            (tests inject in-memory SQLite); ``None`` opens one internally.

    Returns:
        The process exit code (also raised via ``SystemExit``): ``GATE_BLOCK`` (1)
        when ``--gate`` and the verdict is blocking, else ``GATE_OK`` (0).
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="pr_gates.py",
        description="SIPA PR diff gate — assess only the Python files changed on a "
        "branch (git diff vs a base ref) and emit an ALLOW / REVIEW / QUARANTINE "
        "verdict. Static-only: the changed code is never executed.",
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"base ref to diff against (default: {DEFAULT_BASE})")
    parser.add_argument("--cached", action="store_true", help="diff the staged index (git diff --cached) instead of <base>...HEAD")
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", engine.PROVENANCE_AWARE, engine.PROVENANCE_BLIND],
        help="provenance strategy (default: auto)",
    )
    parser.add_argument("--repo-root", default=None, help="repository root to diff in (default: the ICDEV repo root)")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero on a blocking verdict (gate.block_on; QUARANTINE by default) for CI / pre-merge",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    # Honor the feature flag at the CLI seam: a toggled-off canvas no-ops to a pass.
    if not engine.is_enabled():
        result = {
            "assessment_id": None,
            "verdict": "allow",
            "risk_score": 0.0,
            "findings": [],
            "files_assessed": [],
            "skipped": True,
            "reason": f"{engine.FEATURE_FLAG} is off; PR gate skipped",
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"SIPA PR gate skipped — {engine.FEATURE_FLAG} is off")
        sys.exit(engine.GATE_OK)

    result = assess_changed_files(
        base=args.base,
        mode=args.mode,
        conn=conn,
        cached=args.cached,
        repo_root=args.repo_root,
    )

    verdict = str(result["verdict"]).lower()
    block_set, warn_set = engine._gate_verdicts()

    if args.json:
        payload = dict(result)
        if args.gate:
            payload["gate"] = {
                "blocked": verdict in block_set,
                "warned": verdict in warn_set,
                "exit_code": engine.gate_exit_code(verdict),
            }
        print(json.dumps(payload, indent=2))
    else:
        print(f"SIPA PR gate — {result['mode']}")
        print(f"  verdict:        {result['verdict'].upper()}")
        print(f"  risk_score:     {result['risk_score']}")
        print(f"  files_assessed: {len(result['files_assessed'])}")
        print(f"  findings:       {len(result['findings'])}")
        for rel in result["files_assessed"]:
            print(f"    - {rel}")

    code = engine.GATE_OK
    if args.gate:
        code = engine.gate_exit_code(verdict)
        if verdict in block_set:
            print(f"GATE: BLOCKED - verdict {verdict.upper()} is blocking", file=sys.stderr)
        elif verdict in warn_set:
            print(f"GATE: WARN - verdict {verdict.upper()} (non-blocking)", file=sys.stderr)

    sys.exit(code)


if __name__ == "__main__":
    main()
