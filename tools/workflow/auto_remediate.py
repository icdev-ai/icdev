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

from tools.workflow.git_utils import default_branch

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
    # Phantom completion means the agent hallucinated file creation — nothing
    # on disk to fix. Previously in REMEDIABLE as a workaround for false
    # positives caused by _git_worktree_has_real_changes check-2 using
    # BASE_DIR dirty state for unregistered worktrees. That root cause is
    # fixed: check-2 now only fires when task_id is in _worktrees.
    FAILURE_PHANTOM_PATHS,
    FAILURE_BANDIT_SECURITY,
    FAILURE_E2E_REGRESSION,
    FAILURE_UNKNOWN,
}


def classify_failure(reason: str, metrics: Optional[Dict[str, Any]] = None) -> str:
    """Classify a verification failure from its reason text + metrics.

    Order matters — earlier patterns win. Coherence + stale-baseline are
    checked BEFORE bandit/ruff because a "REMEDIATION=bandit_security"
    annotation from a prior attempt can pollute the reason text and
    mis-classify the real failure on retry.
    """
    # Strip any REMEDIATION annotation the previous attempt left behind so
    # we classify on the ORIGINAL failure cause, not on a stale remediation hint.
    r = (reason or "")
    if "| REMEDIATION=" in r:
        r = r.split("| REMEDIATION=")[0]
    r = r.lower()
    m = metrics or {}

    # "No commits" wins — can't fix work that wasn't done
    if "no git commits" in r or "no commits" in r:
        return FAILURE_NO_COMMITS

    # Phantom paths (agent claimed files that don't exist) — can't fix
    if "phantom" in r and "path" in r:
        return FAILURE_PHANTOM_PATHS
    if "claimed 0 file paths" in r:
        return FAILURE_PHANTOM_PATHS

    # Coherence / stale-baseline BEFORE bandit/ruff: a "coherence broken"
    # message may mention ruff/bandit in its details, but the root cause
    # is coherence. Rebasing onto main fixes most of these.
    if "coherence" in r and ("broke" in r or "broken" in r):
        return FAILURE_COHERENCE_BROKEN
    if "stale" in r or "diverg" in r or "not possible to fast-forward" in r:
        return FAILURE_STALE_BASELINE

    # Lint / security issues in the agent's own output (only when not a
    # coherence/stale baseline situation)
    if "ruff found" in r or (m.get("ruff_issues") and m["ruff_issues"] > 0):
        return FAILURE_RUFF_ISSUES
    if "bandit found" in r or (m.get("bandit_issues") and m["bandit_issues"] > 0):
        return FAILURE_BANDIT_SECURITY
    if "e2e" in r and ("fail" in r or "regression" in r):
        return FAILURE_E2E_REGRESSION
    if "manifest" in r:
        return FAILURE_MISSING_MANIFEST

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


def remediate_phantom_paths(cwd: str, task_id: str) -> Tuple[bool, str]:
    """Override the phantom-paths verdict when git shows real file changes.

    The phantom-paths failure fires when the agent output's text heuristic
    couldn't find file-path strings, regardless of what the filesystem
    actually says. This handler consults git directly — if the worktree
    has commits since dispatch OR any uncommitted file changes, the verdict
    was a false positive and we return True so the task proceeds.

    Rationale (memory: feedback_kanban_vv_policy.md): the E3 incident
    (2026-04-15) shipped migration 023_sharepoint to main as commit
    a89e1e10, but the stdout heuristic returned phantom, blocking every
    downstream task until manual unblock. Git is authoritative.
    """
    branch = f"kanban/{task_id}"
    try:
        # 1) Commits on the task branch
        r = _run(
            ["git", "log", f"{default_branch(cwd)}..{branch}", "--name-only", "--pretty=format:"],
            cwd, timeout=10,
        )
        files = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if files:
            preview = ", ".join(sorted(set(files))[:3])
            return True, (
                f"phantom override: {len(set(files))} file(s) changed on {branch} "
                f"(e.g. {preview})"
            )
        # 2) Uncommitted changes in the worktree
        r = _run(["git", "status", "--porcelain"], cwd, timeout=10)
        porcelain = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if porcelain:
            return True, (
                f"phantom override: {len(porcelain)} uncommitted change(s) in worktree"
            )
        # 3) Main advanced (agent's work may have merged already)
        r = _run(
            ["git", "log", default_branch(cwd), "--name-only", "--since=30 minutes ago",
             "--pretty=format:"],
            cwd, timeout=10,
        )
        recent = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if recent:
            return True, (
                f"phantom override: main advanced {len(set(recent))} file(s) "
                "in last 30 min (likely merged)"
            )
    except Exception as exc:
        return False, f"phantom remediation error: {exc}"
    return False, "phantom verdict confirmed: no git-side changes detected"


def remediate_stale_baseline(cwd: str, task_id: str) -> Tuple[bool, str]:
    """Rebase the kanban branch onto current main.

    Safe when no conflicts; aborts cleanly otherwise so the task can go
    to backlog with a useful reason.
    """
    branch = f"kanban/{task_id}"
    try:
        # Attempt rebase from the worktree (which has the branch checked out)
        db = default_branch(cwd)
        r = _run(["git", "rebase", db], cwd, timeout=90)
        if r.returncode == 0:
            out = (r.stdout or "") + (r.stderr or "")
            if "is up to date" in out:
                # Branch is already on top of main — rebase was a no-op.
                # Coherence failure is intrinsic to the task's own changes,
                # not a stale-baseline divergence. Further rebasing won't help.
                return False, (
                    f"{branch} already rebased onto {db} — "
                    "coherence failure is intrinsic to task changes, not stale baseline"
                )
            return True, f"rebased {branch} onto {db}"
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


def _reset_broken_worktree(cwd: str) -> None:
    """Tear down a structurally broken worktree so the next dispatch rebuilds.

    A worktree is "broken" when its cwd is under `.tmp/worktrees/` but
    essential files (e.g. tools/manifest.md) are missing — usually because
    a prior `git worktree remove --force` failed on Windows file locks and
    left an empty orphan dir. Without this reset, `_create_worktree`
    would short-circuit on the existing path and Claude would run in the
    empty dir again, looping forever.
    """
    import shutil
    p = Path(cwd).resolve()
    if ".tmp" not in p.parts or "worktrees" not in p.parts:
        return  # not a worktree — nothing to do
    task_id = p.name
    logger.warning("Resetting broken worktree %s (empty/orphan)", p)
    shutil.rmtree(p, ignore_errors=True)
    for cmd in (
        ["git", "worktree", "prune"],
        ["git", "branch", "-D", f"kanban/{task_id}"],
    ):
        try:
            subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True,
                           text=True, timeout=10)
        except Exception:
            pass


def remediate_missing_manifest(
    cwd: str, reason: str, modified_py: List[str]
) -> Tuple[bool, str]:
    """Append missing tool path(s) to tools/manifest.md in the worktree.

    Parses the reason text and/or falls back to modified_py to identify
    tool paths that need a manifest entry. Adds a minimal placeholder row
    under an "Unclassified" section so the coherence check passes.
    """
    # Manifest was split into shards (2026-04-14). Auto-added entries land in
    # tools/manifest/unclassified.md so the thin index stays clean.
    index_path = Path(cwd) / "tools" / "manifest.md"
    shard_path = Path(cwd) / "tools" / "manifest" / "unclassified.md"
    if not index_path.exists():
        # Structural break: worktree is empty/orphan (not a task-level bug).
        # Actively reset it so the next dispatch rebuilds from a clean HEAD
        # instead of reusing the broken dir and looping forever.
        _reset_broken_worktree(cwd)
        return False, "worktree structurally broken (no tools/manifest.md) — reset; next dispatch will rebuild"

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
        # Combined text: index + all shards — used only for dedup lookup.
        combined_text = index_path.read_text(encoding="utf-8")
        shard_dir = Path(cwd) / "tools" / "manifest"
        if shard_dir.is_dir():
            for s in shard_dir.glob("*.md"):
                combined_text += "\n" + s.read_text(encoding="utf-8")

        added = []
        lines_to_append: List[str] = []
        for tp in missing:
            if tp in combined_text:
                continue
            lines_to_append.append(
                f"| Auto-added {tp.rsplit('/', 1)[-1]} | {tp} | (auto-added by remediation; update description) | --json | stdout |"
            )
            added.append(tp)

        if not lines_to_append:
            return False, "all candidate paths already in manifest"

        changed: List[str] = []
        if shard_path.exists():
            shard_content = shard_path.read_text(encoding="utf-8")
        else:
            shard_dir.mkdir(parents=True, exist_ok=True)
            shard_content = (
                "# Unclassified (auto-added)\n\n"
                "> Shard of `tools/manifest.md`. Entries added by auto-remediation.\n"
                "> Move to the correct topic shard and update descriptions.\n\n"
                "| Tool | File | Description | Input | Output |\n"
                "|------|------|-------------|-------|--------|\n"
            )
            # Add index link.
            index_text = index_path.read_text(encoding="utf-8")
            if "manifest/unclassified.md" not in index_text:
                index_text = index_text.rstrip() + "\n- [Unclassified (auto-added)](manifest/unclassified.md)\n"
                index_path.write_text(index_text, encoding="utf-8")
                changed.append("tools/manifest.md")

        shard_content = shard_content.rstrip() + "\n" + "\n".join(lines_to_append) + "\n"
        shard_path.write_text(shard_content, encoding="utf-8")
        changed.append("tools/manifest/unclassified.md")

        if _git_commit_amend(cwd, changed):
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
    elif failure_type == FAILURE_PHANTOM_PATHS:
        ok, msg = remediate_phantom_paths(cwd, task_id)
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
