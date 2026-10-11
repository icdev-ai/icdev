# CUI // SP-CTI
"""kpr-watch-25: git-first verification survives a scheduler restart.

MEASURED 2026-10-11, the first cycles after kpr-watch-24 went live: the adoption
sweep failed omx-setup-02 "Output too short -- likely no work done" with
git_commits=0, while kanban/omx-setup-02 carried real commits and an open PR.
Arm 1 of `_git_worktree_has_real_changes` ran only when
`_dispatch_main_heads[task_id]` was set -- an in-memory map emptied by every
re-exec -- so for an adopted task the branch was never looked at and the verdict
fell through to a 0-byte worker log.

Real git, as in tests/kanban/test_verify_evidence_is_delivery.py: the defect is
what git is asked, so a mocked git would prove nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.genesis.reflexes import kanban as K

TASK = "omx-x-02"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["git", "clone", str(origin), str(work)],
                   capture_output=True, check=True, timeout=60)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "-u", "origin", "main")
    monkeypatch.setattr(K, "_task_repo_root", lambda tid: work)
    monkeypatch.setattr(K, "_task_base_branch", lambda tid: "main")
    monkeypatch.setattr(K, "_work_dir_for", lambda tid: str(work))
    monkeypatch.setattr(K, "_dir_owns_its_repo_root", lambda d: True)
    # The restarted process: no dispatch baseline, no recorded worktree.
    monkeypatch.setattr(K, "_dispatch_main_heads", {})
    monkeypatch.setattr(K, "_worktrees", {})
    return work


def test_branch_commits_verify_with_no_dispatch_baseline(repo):
    _git(repo, "checkout", "-b", f"kanban/{TASK}")
    (repo / "omarchy_setup.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: setup")

    ok, reason = K._git_worktree_has_real_changes(TASK, committed_only=True)
    assert ok is True, "a restarted scheduler must still see the branch's commits"
    assert f"file(s) changed on kanban/{TASK}" in reason


def test_without_a_baseline_mains_advance_is_not_evidence(repo):
    # Arm 3 must keep requiring the REAL baseline: branch at main, main has
    # other tasks' work -- nothing here is this task's.
    _git(repo, "branch", f"kanban/{TASK}")
    (repo / "other.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: another task")
    _git(repo, "push", "origin", "main")
    _git(repo, "branch", "-f", f"kanban/{TASK}", "main")

    ok, reason = K._git_worktree_has_real_changes(TASK, committed_only=True)
    assert ok is False, reason


def test_an_adopted_task_with_an_empty_log_verifies_git_first(repo, monkeypatch):
    monkeypatch.setattr(K, "_is_dangerous_task", lambda tid: False)
    _git(repo, "checkout", "-b", f"kanban/{TASK}")
    (repo / "omarchy_setup.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: setup")

    ok, reason = K._run_verify_checks(TASK, "")
    assert ok is True, reason
    assert reason.startswith("Verified (git-first)")
