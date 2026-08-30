"""The sweep must look where the worktrees actually are (kpr-dup-12).

`_sweep_old_worktrees` walked exactly one directory -- `WORKTREE_BASE`, the
repo's own `.tmp/worktrees` -- while `tools/git/worktree_paths` had long since
moved every actor to `%TEMP%/icdev-worktrees/<actor>/...`. The sweep was cleaning
the location the path policy ABANDONED.

MEASURED on the live board 2026-08-30, after 292 worktrees had already been
removed by hand:

    total worktrees             39
    under WORKTREE_BASE (swept)  2
    under the sanctioned root   23
    under neither               14

About 5% coverage -- which is why 341 worktrees accumulated while a sweep ran
every half hour and reported nothing to do. Same shape as kpr-dup-11: a cleanup
whose SCOPE makes it structurally unable to find the thing it exists for.

THE SAFETY HALF IS NOT OPTIONAL. Widening the scope alone would take a
FORCE-remover whose only guards were "task not in_progress" and mtime, and point
it at ~25 directories it had never touched. That is precisely the harm kpr-dup-10
exists to prevent, so the sweep now asks `_worktree_is_disposable` first. AGE IS
NOT EVIDENCE OF ABANDONMENT: an old worktree holding unpushed commits is the MOST
valuable one to keep, because nothing else has that work.
"""
from __future__ import annotations

import subprocess

import pytest

from tools.genesis.reflexes import kanban


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


@pytest.fixture()
def repo(tmp_path):
    """A bare remote plus a clone, ready to hang worktrees off."""
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    clone = tmp_path / "clone"
    r = _git("clone", str(remote), str(clone), cwd=tmp_path)
    assert r.returncode == 0, f"git clone failed: {r.stderr[:300]}"
    _git("config", "user.email", "t@example.com", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)
    (clone / "a.txt").write_text("a", encoding="utf-8")
    _git("add", "-A", cwd=clone)
    _git("commit", "-m", "base", cwd=clone)
    _git("push", "-u", "origin", "main", cwd=clone)
    return clone


def _worktree(clone, path, branch):
    _git("branch", branch, cwd=clone)
    _git("push", "-u", "origin", branch, cwd=clone)
    r = _git("worktree", "add", str(path), branch, cwd=clone)
    assert r.returncode == 0, f"worktree add failed: {r.stderr[:300]}"
    return path


# --------------------------------------------------------------------------- #
# scope: the regression
# --------------------------------------------------------------------------- #
def test_the_sanctioned_root_is_walked(monkeypatch, tmp_path, repo):
    """THE regression. Before this, only WORKTREE_BASE was ever listed."""
    sanctioned = tmp_path / "icdev-worktrees"
    nested = sanctioned / "cli" / "session-abc" / "my-slug"
    nested.parent.mkdir(parents=True)
    _worktree(repo, nested, "kanban/xyz-1")

    monkeypatch.setattr(kanban, "WORKTREE_BASE", tmp_path / "legacy-absent")
    monkeypatch.setattr("tools.git.worktree_paths.worktree_root", lambda: sanctioned)

    found = kanban._sweep_candidates()
    assert nested.resolve() in [p.resolve() for p in found], (
        f"the nested worktree under the sanctioned root was not reached: {found}"
    )


def test_container_directories_are_never_candidates(monkeypatch, tmp_path, repo):
    """The actor and session levels are containers. Handing one to
    `git worktree remove` would target a directory holding other people's
    worktrees."""
    sanctioned = tmp_path / "icdev-worktrees"
    nested = sanctioned / "cli" / "session-abc" / "my-slug"
    nested.parent.mkdir(parents=True)
    _worktree(repo, nested, "kanban/xyz-2")

    monkeypatch.setattr(kanban, "WORKTREE_BASE", tmp_path / "legacy-absent")
    monkeypatch.setattr("tools.git.worktree_paths.worktree_root", lambda: sanctioned)

    found = {p.resolve() for p in kanban._sweep_candidates()}
    assert (sanctioned / "cli").resolve() not in found
    assert (sanctioned / "cli" / "session-abc").resolve() not in found


def test_the_legacy_base_still_works(monkeypatch, tmp_path, repo):
    """Widening must not drop the location the sweep already covered."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    wt = _worktree(repo, legacy / "abc-1", "kanban/abc-1")

    monkeypatch.setattr(kanban, "WORKTREE_BASE", legacy)
    monkeypatch.setattr("tools.git.worktree_paths.worktree_root",
                        lambda: tmp_path / "absent-sanctioned")
    assert wt.resolve() in [p.resolve() for p in kanban._sweep_candidates()]


def test_an_unavailable_resolver_does_not_stop_the_legacy_sweep(monkeypatch, tmp_path, repo):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    wt = _worktree(repo, legacy / "abc-2", "kanban/abc-2")

    monkeypatch.setattr(kanban, "WORKTREE_BASE", legacy)

    def boom():
        raise RuntimeError("no resolver")

    monkeypatch.setattr("tools.git.worktree_paths.worktree_root", boom)
    assert wt.resolve() in [p.resolve() for p in kanban._sweep_candidates()]


# --------------------------------------------------------------------------- #
# the task id must come from the BRANCH
# --------------------------------------------------------------------------- #
def test_the_task_id_comes_from_the_branch_not_the_directory_name(tmp_path, repo):
    """`task_id = sub.name` held only for the flat layout. Under the sanctioned
    root a directory is named for a slug, so a name-based guess invents task ids
    that match nothing -- and a task id that matches nothing silently defeats the
    `in_progress` guard, the one thing standing between this sweep and a live
    session's worktree."""
    wt = _worktree(repo, tmp_path / "some-slug-name", "kanban/real-task-9")
    assert kanban._worktree_task_id(wt) == "real-task-9"
    assert wt.name != "real-task-9", "the directory name must not be the task id here"


def test_a_non_kanban_branch_has_no_task(tmp_path, repo):
    wt = _worktree(repo, tmp_path / "featwt", "feat/some-feature")
    assert kanban._worktree_task_id(wt) is None


def test_an_unreadable_worktree_has_no_task(tmp_path):
    assert kanban._worktree_task_id(tmp_path / "does-not-exist") is None


# --------------------------------------------------------------------------- #
# the safety half
# --------------------------------------------------------------------------- #
def test_an_ancient_worktree_with_unpushed_commits_is_kept(monkeypatch, tmp_path, repo):
    """THE safety assertion. Widening the scope points a FORCE-remover at
    directories it never touched; age must not be enough to destroy work that
    exists nowhere else."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    wt = _worktree(repo, legacy / "old-1", "kanban/old-1")
    (wt / "mine.txt").write_text("an afternoon of work", encoding="utf-8")
    _git("add", "-A", cwd=wt)
    _git("commit", "-m", "work that exists nowhere else", cwd=wt)

    listed = _git("worktree", "list", "--porcelain", cwd=wt)
    disposable, why = kanban._worktree_is_disposable(wt, listed)
    assert disposable is False, "an unpushed commit must protect the worktree"
    assert "on no remote" in why

    monkeypatch.setattr(kanban, "WORKTREE_BASE", legacy)
    monkeypatch.setattr("tools.git.worktree_paths.worktree_root",
                        lambda: tmp_path / "absent")
    monkeypatch.setattr(kanban, "_canonical_repo_root", lambda: repo)
    # make it ancient
    import os
    ancient = 1
    os.utime(wt, (ancient, ancient))

    removed_paths = []
    monkeypatch.setattr(kanban, "_remove_worktree",
                        lambda p: removed_paths.append(p) or True)
    monkeypatch.setattr(kanban, "get_connection", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("no board")))

    kanban._sweep_old_worktrees(max_age_days=0)
    assert wt not in removed_paths, "the sweep removed a worktree holding unpushed work"


def test_a_clean_pushed_ancient_worktree_is_removed(monkeypatch, tmp_path, repo):
    """The sweep must still do its job, or widening it achieved nothing."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    wt = _worktree(repo, legacy / "old-2", "kanban/old-2")

    monkeypatch.setattr(kanban, "WORKTREE_BASE", legacy)
    monkeypatch.setattr("tools.git.worktree_paths.worktree_root",
                        lambda: tmp_path / "absent")
    monkeypatch.setattr(kanban, "_canonical_repo_root", lambda: repo)
    import os
    os.utime(wt, (1, 1))

    removed_paths = []
    monkeypatch.setattr(kanban, "_remove_worktree",
                        lambda p: removed_paths.append(p) or True)
    monkeypatch.setattr(kanban, "get_connection", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("no board")))

    kanban._sweep_old_worktrees(max_age_days=0)
    assert wt in removed_paths, "a clean, fully pushed, ancient worktree should be swept"
