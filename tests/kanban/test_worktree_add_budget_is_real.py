"""The worktree-add budget is REAL, and a killed add leaves nothing behind.

THE INCIDENT (kph-repark-kph-repark-mfx-ci-04, measured 2026-09-06).

Between 12:42Z and 15:14Z the scheduler parked SEVEN tasks (twelve parks)
under ``worktree-isolation-guard`` with the same reason: ``git worktree add``
"timed out after 30 seconds". Two of the parked tasks were the REPARK cards
for the first parks. Every leftover on disk was a COMPLETE, CLEAN checkout --
21,400 files, 563 MB, ``git status`` empty -- because the timeout was a fiction:

  * ``git worktree add`` does its checkout in a CHILD process,
    ``git reset --hard --no-recurse-submodules`` (GIT_TRACE=1 shows it);
  * ``subprocess.run(timeout=30)`` killed only the PARENT git.exe at 30s, then
    blocked in ``communicate()`` on the pipe handles the child had inherited
    until the child finished writing the whole tree;
  * so the "timed out" line was logged the moment the worktree was DONE
    (branch created 14:32:54Z, failure logged 14:34:49Z), the guard parked the
    task over a worktree that existed, and the orphaned checkout competed with
    the next dispatch's add, which timed out the same way.

The 30s figure is NOT raised (the card forbids it, and rightly: an add that
needs more than 30s is a slow host or a fat checkout, and patience hides
both). Instead the checkout is parallelised -- measured on the live host,
same tree, same minute: 33.1s and 18.7s single-threaded, 8.5s with
``checkout.workers=0`` -- and the budget becomes real: on expiry the whole
process tree is killed and the partial worktree, its registration and its
branch are removed, so the park describes what is on disk.

These tests use a REAL git repository for the add, because the claim under
test is what git is ASKED and what is left on disk, and a fake for the stuck
add, because making a real checkout hang is not something a test should do.
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import sys
import time

import pytest

from tools.compat.platform_utils import pid_exists

kanban = importlib.import_module("tools.genesis.reflexes.kanban")


def _git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def repo(tmp_path):
    """A small real repository with one commit on ``trunk`` and no remote."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "trunk", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "tools").mkdir()
    (root / "tools" / "manifest.md").write_text("# manifest\n", encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git("add", ".", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


@pytest.fixture
def records(monkeypatch):
    """Capture the module's OWN logger (it does not propagate to caplog)."""
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


def _point_at(monkeypatch, root, target):
    monkeypatch.setattr(kanban, "_task_repo_root", lambda tid: root)
    monkeypatch.setattr(kanban, "_task_base_branch", lambda tid: "trunk")
    monkeypatch.setattr(kanban, "_task_worktree_path", lambda tid: target)


def _is_worktree_add(argv) -> bool:
    return isinstance(argv, (list, tuple)) and "worktree" in argv and "add" in argv


def test_the_budget_is_thirty_seconds_and_named():
    """The card says do NOT raise the 30s timeout. Pin it where it is declared."""
    assert kanban.WORKTREE_ADD_TIMEOUT_SECONDS == 30


def test_the_add_asks_git_for_a_parallel_checkout(repo, records, tmp_path, monkeypatch):
    target = tmp_path / "wts" / "budget-01"
    _point_at(monkeypatch, repo, target)

    seen: list[list[str]] = []
    real_popen = subprocess.Popen

    def spy(argv, *args, **kwargs):
        if _is_worktree_add(argv):
            seen.append(list(argv))
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)

    created = kanban._create_worktree("budget-01")

    assert created == str(target)
    assert (target / ".git").exists(), "git registered the worktree"
    assert (target / "README.md").exists(), "and checked the tree out"
    assert len(seen) == 1, "exactly one add was run"
    argv = seen[0]
    before_subcommand = argv[: argv.index("worktree")]
    assert "-c" in before_subcommand and "checkout.workers=0" in before_subcommand, (
        "the parallel-checkout config must precede the subcommand so git exports "
        "it to the child reset that does the work: %r" % (argv,)
    )
    assert argv[argv.index("add") + 1:][:2] == ["-b", "kanban/budget-01"]

    msg = _messages(records)
    assert "Created worktree for budget-01" in msg
    assert "(budget 30s)" in msg, "the success line carries the measured duration"


def test_a_timed_out_add_is_killed_with_its_children_and_leaves_nothing(
        repo, records, tmp_path, monkeypatch):
    target = tmp_path / "wts" / "budget-02"
    _point_at(monkeypatch, repo, target)
    monkeypatch.setattr(kanban, "WORKTREE_ADD_TIMEOUT_SECONDS", 1)

    killed: list = []
    monkeypatch.setattr(
        kanban, "_kill_process_tree", lambda proc: killed.append(proc) or "fake-kill")

    class _StuckAdd:
        """An add that registered its branch, started writing, and is still
        writing at the deadline -- the live shape, minus the 563 MB."""

        pid = 424242
        returncode = None

        def __init__(self):
            self.calls = 0
            target.mkdir(parents=True)
            (target / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
            (target / "half-written.py").write_text("...", encoding="utf-8")
            _git("branch", "kanban/budget-02", "trunk", cwd=repo)

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="git worktree add", timeout=timeout)
            return "", ""

        def kill(self):  # noqa: D102
            pass

    real_popen = subprocess.Popen

    def fake(argv, *args, **kwargs):
        if _is_worktree_add(argv):
            return _StuckAdd()
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake)

    assert kanban._create_worktree("budget-02") is None

    assert len(killed) == 1 and killed[0].pid == 424242, "the TREE was killed, not proc.kill()"
    assert not target.exists(), "the partial worktree is gone"
    assert _git("rev-parse", "--verify", "--quiet", "kanban/budget-02", cwd=repo).returncode != 0, (
        "the branch the killed add created is gone too")

    msg = _messages(records)
    assert "KILLED" in msg and "fake-kill" in msg
    assert "1s budget" in msg
    assert "was removed" in msg
    assert "neither a longer budget nor a retry" in msg
    # The old line -- a sentence about a worktree that was in fact complete.
    assert "Worktree creation failed for budget-02" not in msg


def test_kill_process_tree_takes_the_children_too():
    """The half that a mock cannot prove: a real parent, a real child, one call."""
    child_src = "import time; time.sleep(60)"
    parent_src = (
        "import subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', %r]); "
        "print(p.pid, flush=True); time.sleep(60)" % child_src
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_src],
        stdout=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        child_pid = int(proc.stdout.readline().strip())
        assert pid_exists(child_pid), "the child was running before the kill"

        how = kanban._kill_process_tree(proc)

        proc.wait(timeout=15)
        deadline = time.monotonic() + 10
        while pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not pid_exists(child_pid), f"child {child_pid} survived: {how}"
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
