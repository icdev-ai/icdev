#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression — _merge_worktree_to_main must never touch the main repo.

On 2026-05-30, _merge_worktree_to_main performed stash/checkout/merge on
BASE_DIR (the main repository working tree).  This caused:

  * User's uncommitted work got stashed
  * The active branch was switched out from under the user
  * Auto-pause had to stop the scheduler whenever a human was editing

The fix uses a temporary git worktree for all merge operations.  This test
pins the corrected behavior:

  * Main repo branch must not change during merge
  * No stash / checkout / merge commands run on the main repo working tree
  * Temp merge worktree must be created, used, and cleaned up
  * The task branch must actually be merged into main
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Patch sys.path so the kanban module is importable
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import tools.genesis.reflexes.kanban as _kanban


def _init_temp_repo(tmp_path: Path) -> Path:
    """Create a throw-away git repo with main + kanban/test-task branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@icdev.local"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Allow pushing to the checked-out branch from a worktree (needed for
    # testing _push_main inside a detached worktree).
    subprocess.run(
        ["git", "config", "receive.denyCurrentBranch", "ignore"],
        cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# init\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Rename to 'main' regardless of git version default (master vs main)
    subprocess.run(
        ["git", "branch", "-m", "main"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Create task branch with a commit
    subprocess.run(
        ["git", "checkout", "-b", "kanban/test-task"],
        cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# task work\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "task commit"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Return to main
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(repo), check=True, capture_output=True,
    )
    return repo


def _git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _push_main_local(cwd: str) -> bool:
    """Test-only push helper: pushes HEAD:main to the local repo itself.

    The production _push_main pushes to 'origin', but test repos have no
    remote.  This helper lets the test verify that the merge actually
    advances the main branch.

    Must return a bool: _merge_worktree_to_main ends with
    `return _push_main(...)`, so a double that returns None makes the whole
    merge report None instead of True.
    """
    proc = subprocess.run(
        ["git", "push", ".", "HEAD:main"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0


@pytest.fixture
def iso_ctx(tmp_path, monkeypatch):
    """Set up isolated repo + patched kanban module constants."""
    repo = _init_temp_repo(tmp_path)
    worktree_base = tmp_path / "worktrees"
    worktree_base.mkdir()

    monkeypatch.setattr(_kanban, "BASE_DIR", repo)
    monkeypatch.setattr(_kanban, "WORKTREE_BASE", worktree_base)
    # Force _default_branch() to recompute for the temp repo
    monkeypatch.setattr(_kanban, "_default_branch_cache", None)
    # Redirect push to the local repo so merges are testable without a remote
    monkeypatch.setattr(_kanban, "_push_main", _push_main_local)

    return {"repo": repo, "worktree_base": worktree_base}


def test_merge_does_not_switch_branch(iso_ctx):
    """_merge_worktree_to_main must not change the current branch of the main repo."""
    repo: Path = iso_ctx["repo"]

    pre_branch = _git(["symbolic-ref", "--short", "HEAD"], repo).stdout.strip()

    ok = _kanban._merge_worktree_to_main("test-task")
    assert ok is True, "Merge should succeed"

    post_branch = _git(["symbolic-ref", "--short", "HEAD"], repo).stdout.strip()
    assert post_branch == pre_branch, (
        f"Main repo branch changed: {pre_branch} -> {post_branch}"
    )


def test_merge_no_stash_or_checkout_on_main_repo(iso_ctx):
    """No stash or checkout operations should run on the main repo working tree.

    We verify this by checking that no stash entries were created and that
    the reflog does not contain checkout entries during the merge window.
    """
    repo: Path = iso_ctx["repo"]

    pre_reflog = _git(["reflog"], repo).stdout.strip()
    pre_stash = _git(["stash", "list"], repo).stdout.strip()

    ok = _kanban._merge_worktree_to_main("test-task")
    assert ok is True

    post_reflog = _git(["reflog"], repo).stdout.strip()
    post_stash = _git(["stash", "list"], repo).stdout.strip()

    assert post_stash == pre_stash, "Stash was created during merge"
    assert post_reflog == pre_reflog, "Reflog changed — checkout or other operation on main repo"


def test_merge_creates_and_cleans_up_temp_worktree(iso_ctx):
    """The temporary merge worktree must be removed after the operation."""
    worktree_base: Path = iso_ctx["worktree_base"]

    ok = _kanban._merge_worktree_to_main("test-task")
    assert ok is True

    merge_wt = worktree_base / ".merge-test-task"
    assert not merge_wt.exists(), "Temp merge worktree was not cleaned up"


def test_merge_actually_merges_commits(iso_ctx):
    """The task branch commit must appear in main after merge."""
    repo: Path = iso_ctx["repo"]

    ok = _kanban._merge_worktree_to_main("test-task")
    assert ok is True

    log = _git(["log", "--oneline", "main"], repo).stdout
    assert "task commit" in log, "Task commit not found in main branch log"


def test_merge_no_op_when_nothing_to_merge(iso_ctx):
    """Merging a branch with zero new commits should return True immediately."""
    repo: Path = iso_ctx["repo"]

    # Create a branch with no commits ahead of main
    _git(["checkout", "-b", "kanban/empty-task"], repo)
    _git(["checkout", "main"], repo)

    ok = _kanban._merge_worktree_to_main("empty-task")
    assert ok is True, "No-op merge should return True"
    # No temp worktree should be created for a no-op
    merge_wt = iso_ctx["worktree_base"] / ".merge-empty-task"
    assert not merge_wt.exists(), "Temp worktree created for no-op merge"
