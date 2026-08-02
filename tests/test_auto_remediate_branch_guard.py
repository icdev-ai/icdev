# CUI // SP-CTI
"""Auto-remediation must never commit to the branch it happens to be standing on.

`remediate_uncommitted_changes` measured `kanban/<task_id>` but staged and
committed in `cwd`, and nothing guaranteed those were the same tree. The kanban
caller resolves cwd as::

    work_dir = _worktrees.get(task_id) or str(BASE_DIR)

`_worktrees` is an in-memory dict, so after any scheduler restart the lookup
misses and cwd falls back to the SHARED CHECKOUT — which sits on main. `git add
-A` then swept up everything every other concurrent session had left uncommitted
there and committed it to local main.

Observed twice on 2026-08-02. Both landed as
``wip: auto-stage N change(s) (coherence-loop escape)`` on main, bundling
unrelated PDC canvas artifacts, and each one blocked every subsequent
fast-forward of the shared checkout until it was untangled by hand.

`_git_commit_amend` had the same exposure through two other remediations and is
worse: it *rewrites* a commit rather than adding one.

These tests drive real git repositories rather than mocks — the defect is
entirely about which branch a real `git commit` lands on.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import auto_remediate as ar  # noqa: E402


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
    )


@pytest.fixture()
def repo(tmp_path):
    """A repo on `main` with one commit, plus a `kanban/task-1` branch."""
    r = tmp_path / "repo"
    r.mkdir()
    _git("init", "-b", "main", cwd=r)
    _git("config", "user.email", "t@example.com", cwd=r)
    _git("config", "user.name", "T", cwd=r)
    (r / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=r)
    _git("commit", "-m", "seed", cwd=r)
    _git("branch", "kanban/task-1", cwd=r)
    return r


def _head_count(repo) -> int:
    out = _git("rev-list", "--count", "HEAD", cwd=repo).stdout.strip()
    return int(out or 0)


def _current_branch(repo) -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).stdout.strip()


# --------------------------------------------------------------------------
# The regression
# --------------------------------------------------------------------------

def test_refuses_to_commit_when_cwd_is_on_main(repo):
    """The exact scenario: cwd fell back to the shared checkout."""
    (repo / "someone_elses_artifact.md").write_text("not mine\n", encoding="utf-8")
    before = _head_count(repo)

    ok, msg = ar.remediate_uncommitted_changes(str(repo), "task-1")

    assert ok is False
    assert "refusing to commit" in msg
    assert "'main'" in msg and "kanban/task-1" in msg
    assert _head_count(repo) == before, "must not add a commit to main"
    assert _git("status", "--porcelain", cwd=repo).stdout.strip(), (
        "the file must be left uncommitted, not swept into a commit"
    )


def test_does_not_stage_other_sessions_files_on_main(repo):
    """`git add -A` is what turned one stray file into a 9-file commit."""
    for name in ("a.md", "b.md", "c.md"):
        (repo / name).write_text(name, encoding="utf-8")
    ar.remediate_uncommitted_changes(str(repo), "task-1")
    staged = _git("diff", "--cached", "--name-only", cwd=repo).stdout.strip()
    assert staged == "", f"nothing should be staged on main, got: {staged!r}"


def test_still_commits_when_cwd_is_the_task_branch(repo):
    """The legitimate path must keep working — this is a guard, not a disable."""
    _git("checkout", "kanban/task-1", cwd=repo)
    (repo / "work.py").write_text("x = 1\n", encoding="utf-8")
    before = _head_count(repo)

    ok, msg = ar.remediate_uncommitted_changes(str(repo), "task-1")

    assert ok is True, msg
    assert _head_count(repo) == before + 1
    assert _current_branch(repo) == "kanban/task-1"
    assert not _git("status", "--porcelain", cwd=repo).stdout.strip()


def test_refuses_on_an_unrelated_branch(repo):
    """Not just main — any branch that is not this task's."""
    _git("checkout", "-b", "kanban/some-other-task", cwd=repo)
    (repo / "f.txt").write_text("f\n", encoding="utf-8")
    ok, msg = ar.remediate_uncommitted_changes(str(repo), "task-1")
    assert ok is False
    assert "refusing to commit" in msg


def test_still_skips_when_the_branch_already_has_commits(repo):
    """Pre-existing guard must survive the new one."""
    _git("checkout", "kanban/task-1", cwd=repo)
    (repo / "first.py").write_text("1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "first", cwd=repo)
    (repo / "second.py").write_text("2\n", encoding="utf-8")

    ok, msg = ar.remediate_uncommitted_changes(str(repo), "task-1")
    assert ok is False
    assert "already has" in msg


def test_no_uncommitted_changes_is_still_a_clean_no_op(repo):
    _git("checkout", "kanban/task-1", cwd=repo)
    ok, msg = ar.remediate_uncommitted_changes(str(repo), "task-1")
    assert ok is False
    assert "no uncommitted changes" in msg


# --------------------------------------------------------------------------
# The amend path — worse, because it rewrites rather than adds
# --------------------------------------------------------------------------

def test_amend_refuses_on_the_default_branch(repo):
    """Amending on main rewrites a commit that is already published."""
    (repo / "seed.txt").write_text("mutated\n", encoding="utf-8")
    head_before = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    assert ar._git_commit_amend(str(repo), []) is False

    assert _git("rev-parse", "HEAD", cwd=repo).stdout.strip() == head_before, (
        "main's HEAD must be untouched"
    )


def test_amend_still_works_on_a_task_branch(repo):
    _git("checkout", "kanban/task-1", cwd=repo)
    (repo / "work.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "work", cwd=repo)
    head_before = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    (repo / "work.py").write_text("x = 2\n", encoding="utf-8")
    assert ar._git_commit_amend(str(repo), ["work.py"]) is True

    assert _git("rev-parse", "HEAD", cwd=repo).stdout.strip() != head_before
    assert _head_count(repo) == 2, "amend must replace, not add"


def test_on_default_branch_fails_closed(tmp_path):
    """A non-repo (or any probe failure) must read as 'on default' and refuse.

    Fail-open here would reinstate the exact bug in the one case we understand
    least.
    """
    assert ar._on_default_branch(str(tmp_path)) is True
    assert ar._git_commit_amend(str(tmp_path), []) is False
