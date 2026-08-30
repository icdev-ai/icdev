"""A directory nothing claims is UNEXPLAINED, not disposable (kpr-dup-10).

THE INCIDENT. On 2026-08-29 three sessions lost work in one night. Dispatch
asked `git worktree list --porcelain` in the task's repo, and when the path was
not in that output it called `shutil.rmtree(path, ignore_errors=True)` over a
directory holding a live session's uncommitted edits.

Two distinct ways the old predicate said "orphan" about a live checkout, and
both are covered below because they need different fixes:

  * it read `listed.stdout` WITHOUT checking `returncode`. A git that fails or
    times out returns empty stdout, and an empty haystack contains no path --
    so EVERY existing directory read as an orphan, exactly when the machine was
    already unhealthy. The predicate did not misjudge one path; it inverted.

  * `_repo_root` is the TASK's repo. A worktree registered against a different
    repository -- an external-repo task, or a CLI session's own checkout on the
    same path -- is correctly absent from that list while entirely alive.

`ignore_errors=True` then made the loss unrecoverable rather than merely
destructive: a partial delete on Windows takes `.git` and leaves the tree, so
the commits stop being reachable from any branch.

THE ASYMMETRY IS THE DESIGN. A wrongly-KEPT directory parks one task, which a
human unsticks in a minute. A wrongly-DELETED one costs work no ordinary means
recovers. So every branch that cannot prove disposability -- including every
error path -- must refuse.
"""
from __future__ import annotations

import subprocess
import types

import pytest

from tools.genesis.reflexes import kanban


def _listed(rc: int = 0, out: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=rc, stdout=out)


# --------------------------------------------------------------------------- #
# the measured inversion
# --------------------------------------------------------------------------- #
def test_a_failed_git_worktree_list_never_licenses_a_delete(tmp_path):
    """THE regression. Empty stdout contains no path, so without the returncode
    check every existing directory reads as an orphan."""
    d = tmp_path / "live"
    (d / ".git").mkdir(parents=True)
    (d / "work.py").write_text("half an afternoon", encoding="utf-8")

    ok, why = kanban._worktree_is_disposable(d, _listed(rc=128, out=""))
    assert ok is False
    assert "failed" in why.lower(), why


def test_a_timed_out_git_is_also_a_refusal(tmp_path):
    """`subprocess.run(timeout=10)` raising is caught upstream and leaves the
    caller holding a result it must not read as authoritative."""
    d = tmp_path / "live"
    d.mkdir()
    (d / "notes.md").write_text("x", encoding="utf-8")
    ok, _ = kanban._worktree_is_disposable(d, _listed(rc=1))
    assert ok is False


# --------------------------------------------------------------------------- #
# a worktree registered against a DIFFERENT repo is alive, not orphaned
# --------------------------------------------------------------------------- #
def test_uncommitted_changes_hold_the_directory(tmp_path, monkeypatch):
    d = tmp_path / "wt"
    (d / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        kanban, "_quiet_git",
        lambda args, cwd: (0, " M tools/foo.py\n?? new.py\n") if args[0] == "status" else (0, ""),
    )
    ok, why = kanban._worktree_is_disposable(d, _listed())
    assert ok is False
    assert "uncommitted" in why


def test_commits_on_no_remote_hold_the_directory(tmp_path, monkeypatch):
    """Clean tree, but the commits exist nowhere else. Deleting this is the
    unrecoverable case -- the work is committed, so it looks safe, and the only
    copy of those objects lives in the directory about to be removed."""
    d = tmp_path / "wt"
    (d / ".git").mkdir(parents=True)

    def fake(args, cwd):
        if args[0] == "status":
            return 0, ""
        return 0, "abc1234 wip: the thing\ndef5678 wip: more\n"

    monkeypatch.setattr(kanban, "_quiet_git", fake)
    ok, why = kanban._worktree_is_disposable(d, _listed())
    assert ok is False
    assert "2 commit" in why, why


def test_an_unreadable_status_is_a_refusal_not_a_pass(tmp_path, monkeypatch):
    d = tmp_path / "wt"
    (d / ".git").mkdir(parents=True)
    monkeypatch.setattr(kanban, "_quiet_git", lambda args, cwd: (128, ""))
    ok, why = kanban._worktree_is_disposable(d, _listed())
    assert ok is False
    assert "could not be read" in why


def test_content_without_a_dot_git_is_a_refusal(tmp_path):
    """Possibly a partial delete that already took .git -- which is exactly the
    state `ignore_errors=True` leaves behind, and exactly when the commits are
    least recoverable."""
    d = tmp_path / "partial"
    d.mkdir()
    (d / "tools").mkdir()
    ok, why = kanban._worktree_is_disposable(d, _listed())
    assert ok is False
    assert "no .git" in why


def test_an_unreadable_directory_is_a_refusal(tmp_path, monkeypatch):
    """An exception must never fall through to 'go ahead and delete'."""
    def boom(_p):
        raise PermissionError("locked")

    monkeypatch.setattr("os.scandir", boom)
    ok, why = kanban._worktree_is_disposable(tmp_path, _listed())
    assert ok is False
    assert "could not inspect" in why


# --------------------------------------------------------------------------- #
# what IS disposable -- the function must still do its job
# --------------------------------------------------------------------------- #
def test_an_empty_directory_is_disposable(tmp_path):
    """The stated target: an orphan empty dir left by a failed `worktree
    remove`, which makes Claude run in an empty cwd."""
    d = tmp_path / "orphan"
    d.mkdir()
    ok, why = kanban._worktree_is_disposable(d, _listed())
    assert ok is True
    assert why == "empty directory"


def test_a_clean_fully_pushed_worktree_is_disposable(tmp_path, monkeypatch):
    d = tmp_path / "wt"
    (d / ".git").mkdir(parents=True)
    monkeypatch.setattr(kanban, "_quiet_git", lambda args, cwd: (0, ""))
    ok, _ = kanban._worktree_is_disposable(d, _listed())
    assert ok is True


# --------------------------------------------------------------------------- #
# the helper it leans on
# --------------------------------------------------------------------------- #
def test_quiet_git_never_raises(monkeypatch):
    def boom(*_a, **_kw):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(subprocess, "run", boom)
    assert kanban._quiet_git(["status"], cwd=".") == (1, "")


def test_every_refusal_carries_a_reason(tmp_path):
    """The reason is returned on BOTH legs so a refusal is auditable. A silent
    skip here is indistinguishable from the delete it replaced."""
    d = tmp_path / "partial"
    d.mkdir()
    (d / "x").write_text("y", encoding="utf-8")
    for listed in (_listed(rc=1), _listed()):
        ok, why = kanban._worktree_is_disposable(d, listed)
        assert ok is False
        assert why and isinstance(why, str) and len(why) > 10


@pytest.mark.parametrize("rc", [1, 128, -1])
def test_no_nonzero_returncode_is_ever_treated_as_authoritative(tmp_path, rc):
    d = tmp_path / "live"
    (d / ".git").mkdir(parents=True)
    assert kanban._worktree_is_disposable(d, _listed(rc=rc))[0] is False
