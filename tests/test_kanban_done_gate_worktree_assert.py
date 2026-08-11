# CUI // SP-CTI
"""The done-gate must not read the shared checkout's dirt as a task's output.

``_git_worktree_has_real_changes`` check 2 accepts a task when ``git status``
in its worktree is non-empty. git has no "this must be a worktree" assertion:
a worktree pruned or removed mid-run leaves its files as an ordinary directory,
and because the kanban worktree base sits under gitignored ``.tmp/``, git does
not error there — it walks UP and answers for the parent repo. The gate then
reads the SHARED CHECKOUT's dirty files as the task's own work.

Observed 2026-08-11 (hgx-vv-01): accepted on 8 companion-sync files that had
been dirty for hours and belonged to no task. Marked done twice on work that
never reached a branch.

These tests build both shapes for real — an actual worktree and a
fallen-through leftover inside a repo — and pin that only the former counts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

kanban = pytest.importorskip("tools.genesis.reflexes.kanban")


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo with one commit and a gitignored .tmp/ dir."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "t@t.t", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    # newline="" so the fixture behaves the same on Windows and Linux.
    with (root / ".gitignore").open("w", encoding="utf-8", newline="") as fh:
        fh.write(".tmp/\n")
    with (root / "seed.txt").open("w", encoding="utf-8", newline="") as fh:
        fh.write("seed\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "seed", cwd=root)
    return root


def test_real_worktree_owns_its_root(repo, tmp_path):
    """A genuine worktree reports itself as toplevel."""
    wt = tmp_path / "wt"
    r = _git("worktree", "add", "-q", "-b", "feat/x", str(wt), "HEAD", cwd=repo)
    assert r.returncode == 0, r.stderr

    assert kanban._dir_owns_its_repo_root(str(wt)) is True


def test_fallen_through_leftover_does_not(repo):
    """A plain dir inside the repo answers for the PARENT — must not count.

    This is the hgx-vv-01 shape: files present, no .git, under gitignored
    .tmp/, and git succeeds there rather than failing.
    """
    leftover = repo / ".tmp" / "worktrees" / "some-task"
    leftover.mkdir(parents=True)
    with (leftover / "work.py").open("w", encoding="utf-8", newline="") as fh:
        fh.write("# real work, unlandable\n")

    # Precondition: git does NOT fail here — it silently answers for the repo.
    top = _git("rev-parse", "--show-toplevel", cwd=leftover)
    assert top.returncode == 0, "expected git to succeed (walk up), not fail"
    assert Path(top.stdout.strip()).resolve() == repo.resolve()

    assert kanban._dir_owns_its_repo_root(str(leftover)) is False


def test_leftover_reports_the_parents_dirt_not_its_own(repo):
    """The precise failure: status in the leftover describes the SHARED repo."""
    leftover = repo / ".tmp" / "worktrees" / "some-task"
    leftover.mkdir(parents=True)

    # Ambient dirt in the shared checkout, belonging to no task.
    with (repo / "seed.txt").open("a", encoding="utf-8", newline="") as fh:
        fh.write("someone else's edit\n")

    status = _git("status", "--porcelain", cwd=leftover)
    dirty = [ln for ln in status.stdout.splitlines() if ln.strip()]
    assert dirty, "fixture should produce ambient dirt"
    assert any("seed.txt" in ln for ln in dirty), dirty

    # That dirt is what the old gate counted. The assert is what rejects it.
    assert kanban._dir_owns_its_repo_root(str(leftover)) is False


def test_repo_root_itself_owns_its_root(repo):
    """BASE_DIR is its own root — the guard is about identity, not location."""
    assert kanban._dir_owns_its_repo_root(str(repo)) is True


@pytest.mark.parametrize("bad", ["", None])
def test_empty_path_is_not_a_worktree(bad):
    assert kanban._dir_owns_its_repo_root(bad) is False


def test_nonexistent_path_is_not_a_worktree(tmp_path):
    assert kanban._dir_owns_its_repo_root(str(tmp_path / "nope")) is False


def test_dir_outside_any_repo_is_not_a_worktree(tmp_path):
    """No enclosing repo: git fails, and failure must read as 'not a worktree'."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert kanban._dir_owns_its_repo_root(str(plain)) is False


# --- the gate itself, which is what actually regressed -----------------------
#
# No dispatch baseline is registered in these two, so checks 1 and 3 are skipped
# and check 2 -- the uncommitted-changes path -- is the only one that can fire.


def test_gate_rejects_ambient_dirt_in_a_fallen_through_worktree(repo, monkeypatch):
    """The hgx-vv-01 regression: shared-checkout dirt must not verify a task."""
    task_id = "tst-fallen-01"
    leftover = repo / ".tmp" / "worktrees" / task_id
    leftover.mkdir(parents=True)

    # Ambient dirt in the shared checkout, belonging to no task.
    with (repo / "seed.txt").open("a", encoding="utf-8", newline="") as fh:
        fh.write("unrelated in-progress work\n")

    monkeypatch.setitem(kanban._worktrees, task_id, str(leftover))
    monkeypatch.delitem(kanban._dispatch_main_heads, task_id, raising=False)

    ok, reason = kanban._git_worktree_has_real_changes(task_id)
    assert ok is False, (
        f"gate accepted a task on the shared checkout's dirty files: {reason!r} — "
        "this is exactly how hgx-vv-01 was marked done twice with nothing on a branch"
    )


def test_gate_still_accepts_real_uncommitted_work_in_a_real_worktree(repo, tmp_path, monkeypatch):
    """The guard must not break the legitimate case it protects."""
    task_id = "tst-real-01"
    wt = tmp_path / "realwt"
    r = _git("worktree", "add", "-q", "-b", f"kanban/{task_id}", str(wt), "HEAD", cwd=repo)
    assert r.returncode == 0, r.stderr

    with (wt / "delivered.py").open("w", encoding="utf-8", newline="") as fh:
        fh.write("# genuine task output\n")

    monkeypatch.setitem(kanban._worktrees, task_id, str(wt))
    monkeypatch.delitem(kanban._dispatch_main_heads, task_id, raising=False)

    ok, reason = kanban._git_worktree_has_real_changes(task_id)
    assert ok is True, "a real worktree with real uncommitted work must still verify"
    assert "uncommitted change" in reason, reason
