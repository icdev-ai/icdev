# CUI // SP-CTI
"""Auto-remediation for kanban task verification failures.

When a task fails validation, classify the failure and attempt a safe
auto-fix BEFORE sending the task to backlog. Only safe, idempotent fixes
are attempted (rebase, ruff --fix, manifest append). Security issues,
E2E failures, and "agent did nothing" cases are NOT auto-remediated —
those require human attention.

Remediation attempts are capped (max 1 per verification cycle) to prevent
infinite loops. If remediation succeeds, the validation pipeline re-runs;
if it passes this time, the task is merged. If remediation fails OR the
re-run still fails, the task goes to backlog with an annotated reason.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

# Failure categories (in priority order of detection)
FAILURE_NO_COMMITS = "no_commits"
FAILURE_PHANTOM_PATHS = "phantom_paths"
FAILURE_STALE_BASELINE = "stale_baseline"
FAILURE_RUFF_ISSUES = "ruff_issues"
FAILURE_MISSING_MANIFEST = "missing_manifest"
FAILURE_BANDIT_SECURITY = "bandit_security"
FAILURE_E2E_REGRESSION = "e2e_regression"
FAILURE_COHERENCE_BROKEN = "coherence_broken"
FAILURE_UNKNOWN = "unknown"

REMEDIABLE = {
    FAILURE_STALE_BASELINE,
    FAILURE_RUFF_ISSUES,
    FAILURE_MISSING_MANIFEST,
    FAILURE_COHERENCE_BROKEN,  # only if due to ruff or manifest
}

UNREMEDIABLE = {
    FAILURE_NO_COMMITS,
    FAILURE_PHANTOM_PATHS,
    FAILURE_BANDIT_SECURITY,
    FAILURE_E2E_REGRESSION,
    FAILURE_UNKNOWN,
}


def classify_failure(reason: str, metrics: Optional[Dict[str, Any]] = None) -> str:
    """Classify a verification failure from its reason text + metrics."""
    r = (reason or "").lower()
    m = metrics or {}

    if "no git commits" in r or "no commits" in r:
        return FAILURE_NO_COMMITS
    if "phantom" in r and "path" in r:
        return FAILURE_PHANTOM_PATHS
    if "claimed 0 file paths" in r:
        return FAILURE_PHANTOM_PATHS
    if "ruff found" in r or (m.get("ruff_issues") and m["ruff_issues"] > 0):
        return FAILURE_RUFF_ISSUES
    if "bandit" in r or (m.get("bandit_issues") and m["bandit_issues"] > 0):
        return FAILURE_BANDIT_SECURITY
    if "e2e" in r and ("fail" in r or "regression" in r):
        return FAILURE_E2E_REGRESSION
    if "manifest" in r:
        return FAILURE_MISSING_MANIFEST
    if "coherence" in r and "broke" in r:
        return FAILURE_COHERENCE_BROKEN
    if "stale" in r or "diverg" in r or "not possible to fast-forward" in r:
        return FAILURE_STALE_BASELINE
    return FAILURE_UNKNOWN


# ---------------------------------------------------------------------------
# Remediation handlers
# ---------------------------------------------------------------------------


def _run(cmd: List[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=cwd, timeout=timeout,
    )


def _git_commit_amend(cwd: str, files: List[str]) -> bool:
    """Stage files and amend the last commit (keeps message)."""
    if files:
        _run(["git", "add"] + files, cwd)
    else:
        _run(["git", "add", "-u"], cwd)
    r = _run(["git", "commit", "--amend", "--no-edit", "--no-verify"], cwd)
    return r.returncode == 0


def remediate_stale_baseline(cwd: str, task_id: str) -> Tuple[bool, str]:
    """Rebase the kanban branch onto current main.

    Safe when no conflicts; aborts cleanly otherwise so the task can go
    to backlog with a useful reason.
    """
    branch = f"kanban/{task_id}"
    try:
        # Attempt rebase from the worktree (which has the branch checked out)
        r = _run(["git", "rebase", "main"], cwd, timeout=90)
        if r.returncode == 0:
            return True, f"rebased {branch} onto main"
        # Conflict — abort to leave a clean state
        _run(["git", "rebase", "--abort"], cwd)
        return False, f"rebase conflict: {r.stderr[:200]}"
    except Exception as exc:
        return False, f"rebase error: {exc}"


def remediate_ruff_issues(cwd: str, modified_py: List[str]) -> Tuple[bool, str]:
    """Run ruff --fix on the agent's modified files and amend commit.

    ruff --fix is safe (no logic changes — removes unused imports, fixes
    whitespace, etc.) and deterministic.
    """
    if not modified_py:
        return False, "no modified .py files to fix"

    try:
        # Apply safe fixes
        _run(
            ["python", "-m", "ruff", "check", "--fix"] + modified_py,
            cwd, timeout=60,
        )
        # ruff returns non-zero if UNSAFE fixes remain — that's fine, we
        # care about what it DID fix
        check = _run(
            ["python", "-m", "ruff", "check"] + modified_py,
            cwd, timeout=30,
        )
        still_broken = check.returncode != 0
        if still_broken:
            remaining = len([ln for ln in check.stdout.splitlines() if ": " in ln])
            return False, f"ruff --fix left {remaining} issues needing manual fix"

        # Amend the kanban branch's last commit with the fixes
        if _git_commit_amend(cwd, modified_py):
            return True, "ruff auto-fixed and amended commit"
        return False, "ruff fixed but amend failed"
    except Exception as exc:
        return False, f"ruff remediation error: {exc}"


def remediate_missing_manifest(
    cwd: str, reason: str, modified_py: List[str]
) -> Tuple[bool, str]:
    """Append missing tool path(s) to tools/manifest.md in the worktree.

    Parses the reason text and/or falls back to modified_py to identify
    tool paths that need a manifest entry. Adds a minimal placeholder row
    under an "Unclassified" section so the coherence check passes.
    """
    manifest_path = Path(cwd) / "tools" / "manifest.md"
    if not manifest_path.exists():
        return False, "tools/manifest.md not in worktree"

    # Find tool paths that need adding
    missing: List[str] = []
    for m in re.finditer(r"tools/[A-Za-z0-9_/\-]+\.py", reason or ""):
        missing.append(m.group(0))
    if not missing:
        # Fall back to agent's newly-added tools under tools/
        missing = [p for p in modified_py if p.startswith("tools/") and p.endswith(".py")]
    if not missing:
        return False, "no candidate tools to add to manifest"

    try:
        content = manifest_path.read_text(encoding="utf-8")
        added = []
        lines_to_append: List[str] = []
        for tp in missing:
            if tp in content:
                continue
            lines_to_append.append(
                f"| Auto-added {tp.rsplit('/', 1)[-1]} | {tp} | (auto-added by remediation; update description) | --json | stdout |"
            )
            added.append(tp)

        if not lines_to_append:
            return False, "all candidate paths already in manifest"

        # Ensure "Unclassified" section exists
        if "## Unclassified (auto-added)" not in content:
            content = content.rstrip() + (
                "\n\n## Unclassified (auto-added)\n"
                "| Tool | File | Description | Input | Output |\n"
                "|------|------|-------------|-------|--------|\n"
            )
        content = content.rstrip() + "\n" + "\n".join(lines_to_append) + "\n"
        manifest_path.write_text(content, encoding="utf-8")

        if _git_commit_amend(cwd, ["tools/manifest.md"]):
            return True, f"added {len(added)} path(s) to manifest"
        return False, "manifest updated but amend failed"
    except Exception as exc:
        return False, f"manifest remediation error: {exc}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def attempt_remediation(
    cwd: str,
    task_id: str,
    reason: str,
    metrics: Optional[Dict[str, Any]] = None,
    modified_files: Optional[List[str]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Classify and attempt to auto-fix a verification failure.

    Returns (remediated: bool, reason: str, info: dict).
    If remediated is True, the caller should re-run validation.
    If False, the task should go to backlog with the returned reason.

    Only safe, idempotent remediations are attempted. Security issues
    (bandit) and runtime regressions (E2E) are never auto-fixed.
    """
    failure_type = classify_failure(reason, metrics)
    info: Dict[str, Any] = {
        "failure_type": failure_type,
        "remediable": failure_type in REMEDIABLE,
    }

    if failure_type in UNREMEDIABLE:
        return False, f"{failure_type} is not auto-remediable — human review needed", info

    if failure_type not in REMEDIABLE:
        return False, f"unknown failure type '{failure_type}' — skipping remediation", info

    # Pick the right handler
    modified_py = [
        f for f in (modified_files or [])
        if f.endswith(".py") and (Path(cwd) / f).exists()
    ]

    if failure_type == FAILURE_STALE_BASELINE:
        ok, msg = remediate_stale_baseline(cwd, task_id)
    elif failure_type == FAILURE_RUFF_ISSUES:
        ok, msg = remediate_ruff_issues(cwd, modified_py)
    elif failure_type == FAILURE_MISSING_MANIFEST:
        ok, msg = remediate_missing_manifest(cwd, reason, modified_py)
    elif failure_type == FAILURE_COHERENCE_BROKEN:
        # Most common case: stale baseline — worktree branched from older
        # main and is missing files added to main since (e.g., new docs,
        # new manifests). Rebase first to pull those in.
        ok, msg = remediate_stale_baseline(cwd, task_id)
        if not ok:
            # Try ruff next — second most common coherence break
            ok, msg = remediate_ruff_issues(cwd, modified_py)
        if not ok:
            # Then try manifest
            ok, msg = remediate_missing_manifest(cwd, reason, modified_py)
    else:
        return False, f"no handler for {failure_type}", info

    info["remediation_attempted"] = True
    info["remediation_success"] = ok
    info["remediation_message"] = msg
    logger.info(
        "remediation for %s (%s): %s — %s",
        task_id, failure_type, "SUCCESS" if ok else "FAILED", msg,
    )
    return ok, msg, info
