# CUI // SP-CTI
# ICDEV Git Operations — branch, commit, push, PR/MR
# Adapted from ADW git_ops.py with GitLab MR support

"""
Git operations for ICDEV CI/CD workflows.

Provides branching, committing, pushing, and PR/MR creation
using git CLI directly (platform-agnostic for git operations)
and VCS abstraction for PR/MR creation.

Usage:
    from tools.ci.modules.git_ops import create_branch, commit_changes, finalize_git_operations
    success, error = create_branch("feat-123-auth")
    success, error = commit_changes("Add authentication module")
    finalize_git_operations(state, logger)
"""

import subprocess
from pathlib import Path
from typing import Tuple, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _git(args: list, cwd: str = None) -> Tuple[str, str, int]:
    """Run a git command."""
    proc = subprocess.run(
        ["git"] + args,
        capture_output=True, text=True,
        cwd=cwd or str(PROJECT_ROOT),
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def create_branch(branch_name: str) -> Tuple[bool, Optional[str]]:
    """Create and checkout a new branch (or checkout existing).

    Adapted from ADW create_branch pattern.
    """
    # Try creating new branch
    _, stderr, rc = _git(["checkout", "-b", branch_name])
    if rc == 0:
        return True, None

    # Branch might already exist — try checking out
    _, stderr2, rc2 = _git(["checkout", branch_name])
    if rc2 == 0:
        return True, None

    return False, f"Failed to create branch '{branch_name}': {stderr}"


def commit_changes(message: str, paths: list = None) -> Tuple[bool, Optional[str]]:
    """Stage changes and commit.

    Args:
        message: Commit message.
        paths: Optional list of specific file paths to stage. If provided,
               only these paths are staged (targeted add). If None, stages
               all tracked modified files with ``git add -u`` (safer than
               ``git add -A`` which also stages untracked files that may
               include sensitive files like .env or credentials).
    """
    if paths:
        # Targeted staging — only specific files
        _, stderr, rc = _git(["add", "--"] + paths)
    else:
        # Stage tracked modified files only (not untracked)
        _, stderr, rc = _git(["add", "-u"])
    if rc != 0:
        return False, f"git add failed: {stderr}"

    # Check if there's anything to commit
    stdout, _, _ = _git(["status", "--porcelain"])
    if not stdout.strip():
        return True, None  # Nothing to commit, still success

    # Commit
    _, stderr, rc = _git(["commit", "-m", message])
    if rc != 0:
        return False, f"git commit failed: {stderr}"

    return True, None


def push_branch(branch_name: str) -> Tuple[bool, Optional[str]]:
    """Push branch to remote.

    Adapted from ADW push_branch pattern.
    """
    _, stderr, rc = _git(["push", "-u", "origin", branch_name])
    if rc != 0:
        return False, f"git push failed: {stderr}"
    return True, None


def get_current_branch() -> Optional[str]:
    """Get the current branch name."""
    stdout, _, rc = _git(["branch", "--show-current"])
    return stdout if rc == 0 else None


def finalize_git_operations(state, logger, vcs=None):
    """Push changes and create/update PR or MR.

    Adapted from ADW finalize_git_operations pattern with dual platform support.

    Args:
        state: ICDevState instance
        logger: Logger instance
        vcs: VCS instance (auto-created if None)
    """
    branch_name = state.get("branch_name")
    issue_number = state.get("issue_number")

    if not branch_name:
        logger.warning("No branch name in state, skipping finalize")
        return

    # Import VCS if needed
    if vcs is None:
        from tools.ci.modules.vcs import VCS
        try:
            vcs = VCS()
        except Exception as e:
            logger.error(f"Cannot initialize VCS: {e}")
            return

    # Push
    logger.info(f"Pushing branch {branch_name}...")
    success, error = push_branch(branch_name)
    if not success:
        logger.error(f"Push failed: {error}")
        if issue_number:
            vcs.comment_on_issue(int(issue_number), f"[ICDEV-BOT] Push failed: {error}")
        return

    logger.info("Push successful")

    # Check if PR/MR exists
    pr_url = vcs.check_pr_exists(branch_name)
    if pr_url:
        logger.info(f"PR/MR already exists: {pr_url}")
        if issue_number:
            platform_name = "MR" if vcs.is_gitlab else "PR"
            vcs.comment_on_issue(
                int(issue_number),
                f"[ICDEV-BOT] Updated existing {platform_name}: {pr_url}"
            )
        return

    # Create new PR/MR
    logger.info("Creating new PR/MR...")
    run_id = state.get("run_id", "unknown")
    title = f"ICDEV-{run_id}: Issue #{issue_number}" if issue_number else f"ICDEV-{run_id}"
    body = (
        f"## Summary\n"
        f"Automated by ICDEV workflow run `{run_id}`\n\n"
        f"Closes #{issue_number}\n\n"
        f"## CUI // SP-CTI\n"
        f"Generated by ICDEV CI/CD system.\n"
    )

    pr_url = vcs.create_pr(title=title, body=body, head=branch_name)
    if pr_url:
        platform_name = "Merge Request" if vcs.is_gitlab else "Pull Request"
        logger.info(f"{platform_name} created: {pr_url}")
        if issue_number:
            vcs.comment_on_issue(
                int(issue_number),
                f"[ICDEV-BOT] Created {platform_name}: {pr_url}"
            )
    else:
        logger.error("Failed to create PR/MR")
