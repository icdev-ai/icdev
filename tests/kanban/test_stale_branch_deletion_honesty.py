"""``_create_worktree`` must not claim it deleted a branch it did not delete.

THE INCIDENT (kph-repark-fni-ana-01, measured 2026-09-04/05).

``fni-ana-01`` is an external-repo task: the ICDev scheduler dispatches it, and
the worker it launches builds in ICDEV[FT]. The two disagree about WHERE:

    dispatcher   tempfile.gettempdir()/icdev-kanban/<repo>/<task>
                 (_task_worktree_path, for an external task)
    worker       tempfile.gettempdir()/icdev-worktrees/kanban/<task>
                 (tools.git.worktree_paths, what CLAUDE.md tells it to use)

So on every retry the dispatcher looks in its own place, finds nothing, and
treats the LIVE worker worktree's branch as a stale leftover to be cleaned up.
``git branch -D`` then refuses -- the branch is checked out -- and the
``update-ref -d`` fallback refuses for the same reason.

That refusal is the only thing that saved the work: the branch held two commits
and 1,179 lines the worker had not yet pushed.

The defect this module pins is what the code SAID about it. The fallback's
return code was not checked, and the line after it logged

    Stale branch kanban/fni-ana-01 deleted via update-ref fallback

unconditionally. A human read that at 03:16:51 on 2026-09-05, recorded the
cause as "a transient 'git worktree add' failure" (their requeue reason,
verbatim), and requeued the task. It reparked identically ELEVEN SECONDS later.
A message asserting the blocker was gone is what made a permanent condition
look transient.

These tests use a REAL git repository with a REAL held branch, because the
whole defect is a return code from git that a mock would have to be told to
produce -- and being told is exactly what was missing.
"""
from __future__ import annotations

import importlib
import logging
import subprocess

import pytest

kanban = importlib.import_module("tools.genesis.reflexes.kanban")


def _git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


@pytest.fixture
def repo_with_held_branch(tmp_path):
    """A repo where ``kanban/ext-held-01`` is checked out in a live worktree.

    This is the live-worker shape, not a corrupted one: the worktree directory
    EXISTS, so ``git worktree prune`` correctly leaves the registration alone
    and git correctly refuses to delete the branch out from under it.
    """
    root = tmp_path / "extrepo"
    root.mkdir()
    _git("init", "-b", "trunk", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "seed", cwd=root)

    held = tmp_path / "worker-worktree"
    add = _git("worktree", "add", "-b", "kanban/ext-held-01", str(held), "trunk", cwd=root)
    assert add.returncode == 0, add.stderr
    # The worker's unpushed work. If the cleanup ever succeeds, this is what is lost.
    (held / "work.txt").write_text("1179 lines of finished work", encoding="utf-8")
    _git("add", "-A", cwd=held)
    _git("commit", "-m", "the work", cwd=held)
    return root, held


@pytest.fixture
def records(monkeypatch):
    """Capture the module's OWN logger.

    ``kanban`` uses ``get_logger()``, whose logger does not propagate, so
    ``caplog`` sees nothing from it. Attach to the module logger directly.
    """
    captured: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):  # noqa: D102
            captured.append(record)

    handler = _Collect()
    kanban.logger.addHandler(handler)
    try:
        yield captured
    finally:
        kanban.logger.removeHandler(handler)


def _messages(records) -> str:
    return "\n".join(r.getMessage() for r in records)


def _run(repo_root, missing_path, monkeypatch):
    monkeypatch.setattr(kanban, "_task_repo_root", lambda tid: repo_root)
    monkeypatch.setattr(kanban, "_task_base_branch", lambda tid: "trunk")
    monkeypatch.setattr(kanban, "_task_worktree_path", lambda tid: missing_path)
    return kanban._create_worktree("ext-held-01")


def test_it_does_not_claim_a_deletion_that_did_not_happen(
        repo_with_held_branch, records, tmp_path, monkeypatch):
    """RED before the fix: the old code logs 'deleted via update-ref fallback'."""
    root, _held = repo_with_held_branch
    missing = tmp_path / "dispatcher-place" / "ext-held-01"
    assert not missing.exists()

    result = _run(root, missing, monkeypatch)

    assert result is None, "creation must still fail -- behaviour is unchanged"
    assert "deleted via update-ref fallback" not in _messages(records), (
        "the branch is still there; claiming it was deleted is what made a "
        "permanent condition read as transient"
    )


def test_the_branch_and_its_work_survive(repo_with_held_branch, tmp_path, monkeypatch):
    """The refusal is load-bearing: the branch holds unpushed work."""
    root, _held = repo_with_held_branch
    before = _git("rev-parse", "kanban/ext-held-01", cwd=root).stdout.strip()

    _run(root, tmp_path / "dispatcher-place" / "ext-held-01", monkeypatch)

    after = _git("rev-parse", "kanban/ext-held-01", cwd=root)
    assert after.returncode == 0, "the held branch must not be deleted"
    assert after.stdout.strip() == before, "and must not be moved"


def test_the_refusal_names_the_holder_and_says_it_is_not_transient(
        repo_with_held_branch, records, tmp_path, monkeypatch):
    """A reader must get the cause, not a denial -- that is the whole repair."""
    root, held = repo_with_held_branch

    _run(root, tmp_path / "dispatcher-place" / "ext-held-01", monkeypatch)

    text = _messages(records)
    assert "Refusing to delete branch" in text
    assert "NOT transient" in text, "the misdiagnosis this fix exists to prevent"
    assert "kanban/ext-held-01" in text, "name the branch"
    assert held.name in text, "name the worktree that holds it"


def test_update_ref_is_not_used_against_a_live_worktree(
        repo_with_held_branch, tmp_path, monkeypatch):
    """The data-loss path itself.

    ``git branch -D`` refuses a checked-out branch; ``git update-ref -d`` is
    plumbing and does NOT (measured rc=0 on git 2.55.0.windows.2). Forcing it
    through orphans the worker's commits, and the failed ``worktree add`` then
    recreates the NAME at the base commit -- so the branch reads intact while
    pointing somewhere else.
    """
    root, held = repo_with_held_branch
    work = _git("rev-parse", "kanban/ext-held-01", cwd=root).stdout.strip()
    base = _git("rev-parse", "trunk", cwd=root).stdout.strip()
    assert work != base, "fixture must actually carry a commit"

    _run(root, tmp_path / "dispatcher-place" / "ext-held-01", monkeypatch)

    now = _git("rev-parse", "--verify", "kanban/ext-held-01", cwd=root).stdout.strip()
    assert now == work, "the worker's commit must remain reachable from the branch"
    assert now != base, "and the branch must not be silently reset to the base"
    # The worktree is untouched and can still commit and push.
    assert (held / "work.txt").exists()


def test_a_genuinely_stale_branch_is_still_cleaned_up(tmp_path, monkeypatch):
    """No regression: an abandoned branch nothing holds is still removed.

    The fix only changes what is REPORTED when deletion fails. A branch with no
    worktree behind it must still be deleted and the worktree still created --
    otherwise every retry of an ordinary task would wedge.
    """
    root = tmp_path / "plainrepo"
    root.mkdir()
    _git("init", "-b", "trunk", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    _git("branch", "kanban/ext-stale-01", "trunk", cwd=root)

    target = tmp_path / "fresh" / "ext-stale-01"
    monkeypatch.setattr(kanban, "_task_repo_root", lambda tid: root)
    monkeypatch.setattr(kanban, "_task_base_branch", lambda tid: "trunk")
    monkeypatch.setattr(kanban, "_task_worktree_path", lambda tid: target)

    result = kanban._create_worktree("ext-stale-01")

    assert result == str(target), "an unheld stale branch must still be recreated"
    assert target.exists()
