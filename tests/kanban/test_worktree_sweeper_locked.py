# CUI // SP-CTI
"""The worktree sweeper must not report removals it did not perform.

The bug, found while reconciling a bad grep:

    git worktree list | wc -l   ->  233 worktrees, 97 of them LOCKED

The sweeper runs every cycle and logs "Sweep: removed stale worktree ..." — and had been
doing so for months while the count only went up.

It ran ``git worktree remove <path> --force`` and never looked at the return code.
``subprocess.run`` does not raise on a non-zero exit, so the ``except`` around it never
fired. And git REFUSES to remove a locked worktree even with --force:

    fatal: cannot remove a locked working tree;
    use 'remove -f -f' to override or unlock first          (rc=128)

So the sweeper counted the removal, logged the removal, and the worktree was still there.
The log said the cleanup was working the entire time it was not.

**A cleanup routine that cannot fail is one that cannot clean up.**

These tests use REAL git worktrees and a REAL lock, because that is the only thing that
would have caught it — a mocked subprocess returns whatever you tell it to.
"""
from __future__ import annotations

import importlib
import subprocess

import pytest

kanban = importlib.import_module("tools.genesis.reflexes.kanban")


@pytest.fixture(autouse=True)
def _confine_the_sweep_to_this_test(tmp_path, monkeypatch):
    """The sweeper walks TWO roots, and patching one of them is not isolation.

    THE DEFECT THIS FIXTURE EXISTS FOR, observed by running this file: every test here
    monkeypatches ``kanban.WORKTREE_BASE`` and nothing else, but ``_sweep_roots`` returns
    WORKTREE_BASE **and** the sanctioned root from ``worktree_paths.worktree_root()`` --
    the second one added deliberately, because sweeping only the repo-local directory
    covered about 5% of real worktrees. So this suite reached straight past its fixture
    into ``%TEMP%/icdev-worktrees`` and DELETED THE DEVELOPER'S OWN WORKTREES, then failed
    its own count assertion because ``removed`` carried their names too.

    A test that force-removes real work as a side effect is worse than a failing one. The
    sanctioned root is redirected at a per-test temp directory, so ``_sweep_roots`` still
    runs its real logic and simply finds nothing outside the fixture.
    """
    import tools.git.worktree_paths as wp

    sanctioned = tmp_path / "sanctioned-root"
    sanctioned.mkdir()
    monkeypatch.setattr(wp, "worktree_root", lambda *a, **k: sanctioned)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repo whose worktrees the sweeper will act on."""
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args, cwd=root):
        return subprocess.run(["git", *args], cwd=str(cwd),
                              capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (root / "README.md").write_text("hi", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    monkeypatch.setattr(kanban, "BASE_DIR", root)
    return root, git


def _worktrees(git) -> str:
    return git("worktree", "list").stdout


def test_a_LOCKED_worktree_is_actually_removed(repo):
    """--force alone does NOT remove a locked worktree. This is the 97."""
    root, git = repo
    wt = root / ".tmp" / "worktrees" / "stale-01"
    git("worktree", "add", "-q", "--detach", str(wt), "HEAD")
    git("worktree", "lock", str(wt))

    assert "stale-01" in _worktrees(git)
    # Confirm the premise rather than assuming it: plain --force is refused.
    refused = git("worktree", "remove", str(wt), "--force")
    assert refused.returncode != 0
    assert "locked" in refused.stderr.lower()

    assert kanban._remove_worktree(wt) is True
    assert "stale-01" not in _worktrees(git)
    assert not wt.exists()


def test_an_unlocked_worktree_is_removed_normally(repo):
    root, git = repo
    wt = root / ".tmp" / "worktrees" / "plain-01"
    git("worktree", "add", "-q", "--detach", str(wt), "HEAD")

    assert kanban._remove_worktree(wt) is True
    assert "plain-01" not in _worktrees(git)


def test_a_removal_that_FAILS_returns_False(repo):
    """The whole bug in one assertion. The old code returned nothing and the caller
    counted it as removed regardless — so the sweep log was fiction."""
    root, _git = repo
    assert kanban._remove_worktree(root / "not" / "a" / "worktree") is False


def test_the_sweeper_only_counts_what_it_really_removed(repo, monkeypatch):
    """It must never again append to `removed` for a worktree that is still on disk."""
    import time

    root, git = repo
    base = root / ".tmp" / "worktrees"
    for name in ("old-locked", "old-plain"):
        wt = base / name
        git("worktree", "add", "-q", "--detach", str(wt), "HEAD")
    git("worktree", "lock", str(base / "old-locked"))

    monkeypatch.setattr(kanban, "WORKTREE_BASE", base)
    # Age them past the staleness threshold.
    old = time.time() - (400 * 86400)
    for name in ("old-locked", "old-plain"):
        import os
        os.utime(base / name, (old, old))

    removed = kanban._sweep_old_worktrees(max_age_days=1)

    assert set(removed) == {"old-locked", "old-plain"}
    listing = _worktrees(git)
    assert "old-locked" not in listing, "the locked one must be GONE, not just reported"
    assert "old-plain" not in listing


def test_an_in_progress_task_is_never_swept(repo, monkeypatch):
    """A live agent's worktree is not cruft, however old the directory looks."""
    import os
    import time

    root, git = repo
    base = root / ".tmp" / "worktrees"
    wt = base / "live-01"
    git("worktree", "add", "-q", "--detach", str(wt), "HEAD")
    old = time.time() - (400 * 86400)
    os.utime(wt, (old, old))

    monkeypatch.setattr(kanban, "WORKTREE_BASE", base)

    class _Conn:
        def execute(self, *_a, **_kw):
            return self

        def fetchall(self):
            return [{"id": "live-01"}]

        def close(self):
            pass

    monkeypatch.setattr(kanban, "get_connection", lambda *a, **kw: _Conn())

    assert kanban._sweep_old_worktrees(max_age_days=1) == []
    assert "live-01" in _worktrees(git), "a live agent's worktree must survive"


# ---------------------------------------------------------------------------
# The same bug, one layer down: prune also refuses to touch a LOCKED entry
# ---------------------------------------------------------------------------
def test_a_locked_entry_whose_directory_is_GONE_is_still_pruned(repo):
    """Unreachable by every cleanup path we had.

    `git worktree prune` skips a locked entry, and the sweeper only walks directories
    that exist — so an entry that is BOTH locked AND whose directory has been deleted
    stays in `git worktree list` forever. 26 of them were still being reported after a
    sweep that had genuinely removed everything it could reach.

    Unlocking is unambiguously safe here: the working tree is gone. There is nothing left
    to protect and nothing that can be lost.
    """
    import shutil

    root, git = repo
    wt = root / ".tmp" / "worktrees" / "dead-01"
    git("worktree", "add", "-q", "--detach", str(wt), "HEAD")
    git("worktree", "lock", str(wt))
    shutil.rmtree(wt)                     # the directory is gone; the entry is not

    assert "dead-01" in _worktrees(git)
    git("worktree", "prune")
    assert "dead-01" in _worktrees(git), "prune alone cannot clear a locked dead entry"

    assert kanban._unlock_dead_entries() >= 1
    git("worktree", "prune")

    assert "dead-01" not in _worktrees(git)


def test_a_LIVE_locked_worktree_keeps_its_lock(repo):
    """We unlock dead POINTERS, never a directory that still exists — that lock may be a
    live agent's claim on its worktree."""
    root, git = repo
    wt = root / ".tmp" / "worktrees" / "alive-01"
    git("worktree", "add", "-q", "--detach", str(wt), "HEAD")
    git("worktree", "lock", str(wt))

    kanban._unlock_dead_entries()

    assert "locked" in _worktrees(git), "a live worktree must keep its lock"
    assert wt.exists()
