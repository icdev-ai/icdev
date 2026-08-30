"""The disposability probe asks about THIS worktree, not the whole repo (kpr-dup-11).

kpr-dup-10 added `_worktree_is_disposable` so dispatch could not rmtree a live
session's worktree. Its unpushed-commit probe was:

    git log --branches --not --remotes --oneline

`--branches` means EVERY branch in the repository. A worktree SHARES its .git
with the main checkout, so that query returns the same repo-wide answer from
every worktree.

MEASURED 2026-08-30 in C:/AI/ICDev/.tmp/worktrees/kpr-dup-10, whose branch was
merged and pushed:

    git log --branches --not --remotes --oneline | wc -l   -> 2142
    git log HEAD       --not --remotes --oneline | wc -l   ->    0

So the helper reported "a git worktree holding 2142 commit(s) that are on no
remote" and refused, for a worktree holding nothing of its own.

THE DIRECTION OF THE ERROR WAS SAFE AND THE CONSEQUENCE WAS NOT. Nothing was
destroyed -- it over-refused. But on any active repository at least one branch
always has an unpushed commit, so `disposable` was UNREACHABLE and the cleanup
path was dead, re-opening the leak `reclaim_worktree` exists for (122
registered worktrees, recursively nested, measured 2026-08-02). A guard that can
never PASS is the same defect as one that never FIRES, mirrored.

THESE TESTS BUILD A REAL REPOSITORY rather than stubbing `_quiet_git`. A stub
would be written against whichever query the source happens to make, so it would
pass against both the old and the new code and prove nothing. The distinction
only exists in git's own behaviour, so git is what has to answer.
"""
from __future__ import annotations

import subprocess

import pytest

from tools.genesis.reflexes import kanban


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )


@pytest.fixture()
def repo_with_a_pushed_worktree(tmp_path):
    """A remote, a clone whose OTHER branch has unpushed commits, and a worktree
    whose own HEAD is fully pushed.

    That is the shape the bug lives in, and it is the ordinary shape of this
    repository on any working day.
    """
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    clone = tmp_path / "clone"
    r = _git("clone", str(remote), str(clone), cwd=tmp_path)
    # ASSERT, never skip. A skip here could not fire on CI (git is what the
    # runner checks the repo out with), so it would be a guard that never
    # fires -- and it would let the whole file report coverage while asserting
    # nothing on any machine where git broke.
    assert r.returncode == 0, f"git clone failed: {r.stderr[:300]}"
    _git("config", "user.email", "t@example.com", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)

    (clone / "a.txt").write_text("a", encoding="utf-8")
    _git("add", "-A", cwd=clone)
    _git("commit", "-m", "base", cwd=clone)
    _git("push", "-u", "origin", "main", cwd=clone)

    # A feature branch whose commit is pushed -- this becomes the worktree.
    _git("checkout", "-b", "feature", cwd=clone)
    (clone / "b.txt").write_text("b", encoding="utf-8")
    _git("add", "-A", cwd=clone)
    _git("commit", "-m", "feature work", cwd=clone)
    _git("push", "-u", "origin", "feature", cwd=clone)
    _git("checkout", "main", cwd=clone)

    # A DIFFERENT branch with commits on no remote. This is what `--branches`
    # sees and what HEAD-scoped counting correctly ignores.
    _git("checkout", "-b", "unpushed-elsewhere", cwd=clone)
    (clone / "c.txt").write_text("c", encoding="utf-8")
    _git("add", "-A", cwd=clone)
    _git("commit", "-m", "never pushed", cwd=clone)
    _git("checkout", "main", cwd=clone)

    wt = tmp_path / "wt"
    r = _git("worktree", "add", str(wt), "feature", cwd=clone)
    assert r.returncode == 0, f"git worktree add failed: {r.stderr[:300]}"
    return clone, wt


def test_the_two_queries_genuinely_disagree(repo_with_a_pushed_worktree):
    """The premise, asserted rather than assumed. If this ever stops holding,
    the tests below stop testing anything and should fail loudly here first."""
    _clone, wt = repo_with_a_pushed_worktree
    broad = _git("log", "--branches", "--not", "--remotes", "--oneline", cwd=wt)
    scoped = _git("log", "HEAD", "--not", "--remotes", "--oneline", cwd=wt)
    assert broad.stdout.strip(), "the repo-wide query should see the unpushed branch"
    assert not scoped.stdout.strip(), "this worktree's HEAD is fully pushed"


def test_a_pushed_worktree_is_disposable_despite_unpushed_work_elsewhere(
    repo_with_a_pushed_worktree,
):
    """THE REGRESSION. Before this fix the helper counted the OTHER branch's
    commits and refused, so no worktree on an active repo was ever cleanable."""
    _clone, wt = repo_with_a_pushed_worktree
    listed = _git("worktree", "list", "--porcelain", cwd=wt)
    ok, why = kanban._worktree_is_disposable(wt, listed)
    assert ok is True, f"refused a fully-pushed worktree: {why}"


def test_a_worktree_holding_its_own_unpushed_commit_is_still_held(
    repo_with_a_pushed_worktree,
):
    """The narrowing must not cost the protection kpr-dup-10 added. Commit
    inside the worktree and it must be refused again."""
    _clone, wt = repo_with_a_pushed_worktree
    (wt / "mine.txt").write_text("an afternoon of work", encoding="utf-8")
    _git("add", "-A", cwd=wt)
    _git("commit", "-m", "work that exists nowhere else", cwd=wt)

    listed = _git("worktree", "list", "--porcelain", cwd=wt)
    ok, why = kanban._worktree_is_disposable(wt, listed)
    assert ok is False
    assert "on no remote" in why, why


def test_uncommitted_changes_are_still_held(repo_with_a_pushed_worktree):
    """The other half of kpr-dup-10's protection, over a real repo this time."""
    _clone, wt = repo_with_a_pushed_worktree
    (wt / "dirty.txt").write_text("mid-edit", encoding="utf-8")

    listed = _git("worktree", "list", "--porcelain", cwd=wt)
    ok, why = kanban._worktree_is_disposable(wt, listed)
    assert ok is False
    assert "uncommitted" in why, why
