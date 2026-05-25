# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/modules/git_ops.py.

The git CLI is fully mocked — no real subprocess calls, no real remote.
All tests verify the public contract documented in
docs/rewrite/adw/specs/tools/ci/modules/git_ops.md.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.modules import git_ops  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# _run_git command shape (no live git)
# ────────────────────────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_subprocess(side_effect):
    return patch("tools.ci.modules.git_ops.subprocess.run", side_effect=side_effect)


# ────────────────────────────────────────────────────────────────────────────
# create_branch
# ────────────────────────────────────────────────────────────────────────────


def test_create_branch_new_succeeds():
    seen = []

    def fake(args, **kw):
        seen.append(args)
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.create_branch("feat-x")
    assert ok is True
    assert err is None
    # The first call must be checkout -b
    assert seen[0][:3] == ["git", "checkout", "-b"]
    assert "feat-x" in seen[0]


def test_create_branch_falls_back_to_checkout():
    calls = []

    def fake(args, **kw):
        calls.append(args)
        if len(calls) == 1:
            return _FakeProc(stderr="already exists", returncode=1)
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.create_branch("feat-y")
    assert ok is True
    assert err is None
    assert len(calls) == 2
    assert calls[1][:3] == ["git", "checkout", "feat-y"]


def test_create_branch_total_failure_returns_reason():
    def fake(args, **kw):
        return _FakeProc(stderr="permission denied", returncode=128)

    with _patch_subprocess(fake):
        ok, err = git_ops.create_branch("feat-z")
    assert ok is False
    assert "permission denied" in (err or "")
    assert "feat-z" in (err or "")


# ────────────────────────────────────────────────────────────────────────────
# commit_changes
# ────────────────────────────────────────────────────────────────────────────


def test_commit_changes_uses_add_u_by_default():
    calls = []

    def fake(args, **kw):
        calls.append(args)
        if args[1] == "add":
            return _FakeProc()
        if args[1] == "status":
            return _FakeProc(stdout=" M file.py")
        if args[1] == "commit":
            return _FakeProc()
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.commit_changes("test message")
    assert ok is True
    assert err is None
    assert calls[0] == ["git", "add", "-u"]
    # Critical safety: never -A
    for c in calls:
        assert "-A" not in c, f"git add -A is forbidden: {c}"


def test_commit_changes_with_paths_uses_targeted_add():
    calls = []

    def fake(args, **kw):
        calls.append(args)
        if args[1] == "add":
            return _FakeProc()
        if args[1] == "status":
            return _FakeProc(stdout=" M a.py")
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.commit_changes("msg", paths=["a.py", "b.py"])
    assert ok is True
    assert calls[0] == ["git", "add", "--", "a.py", "b.py"]


def test_commit_changes_clean_tree_is_successful_noop():
    def fake(args, **kw):
        if args[1] == "add":
            return _FakeProc()
        if args[1] == "status":
            return _FakeProc(stdout="")
        if args[1] == "commit":
            raise AssertionError("commit should not run on clean tree")
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.commit_changes("msg")
    assert ok is True
    assert err is None


def test_commit_changes_add_failure_returns_reason():
    def fake(args, **kw):
        if args[1] == "add":
            return _FakeProc(stderr="pathspec error", returncode=1)
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.commit_changes("msg")
    assert ok is False
    assert "pathspec error" in (err or "")


def test_commit_changes_commit_failure_returns_reason():
    def fake(args, **kw):
        if args[1] == "add":
            return _FakeProc()
        if args[1] == "status":
            return _FakeProc(stdout=" M x")
        if args[1] == "commit":
            return _FakeProc(stderr="hook rejected", returncode=1)
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.commit_changes("msg")
    assert ok is False
    assert "hook rejected" in (err or "")


# ────────────────────────────────────────────────────────────────────────────
# push_branch / get_current_branch
# ────────────────────────────────────────────────────────────────────────────


def test_push_branch_success():
    calls = []

    def fake(args, **kw):
        calls.append(args)
        return _FakeProc()

    with _patch_subprocess(fake):
        ok, err = git_ops.push_branch("feat-q")
    assert ok is True
    assert calls[0] == ["git", "push", "-u", "origin", "feat-q"]


def test_push_branch_never_uses_force():
    """Spec rule: no --force / -f / --force-with-lease."""
    src = pathlib.Path(git_ops.__file__).read_text(encoding="utf-8")
    assert "--force" not in src
    assert "force-with-lease" not in src


def test_push_branch_failure_returns_reason():
    def fake(args, **kw):
        return _FakeProc(stderr="rejected", returncode=1)

    with _patch_subprocess(fake):
        ok, err = git_ops.push_branch("feat-q")
    assert ok is False
    assert "rejected" in (err or "")


def test_get_current_branch_returns_trimmed_name():
    def fake(args, **kw):
        return _FakeProc(stdout="main\n")

    with _patch_subprocess(fake):
        assert git_ops.get_current_branch() == "main"


def test_get_current_branch_returns_none_on_error():
    def fake(args, **kw):
        return _FakeProc(returncode=1)

    with _patch_subprocess(fake):
        assert git_ops.get_current_branch() is None


# ────────────────────────────────────────────────────────────────────────────
# finalize_git_operations
# ────────────────────────────────────────────────────────────────────────────


class _FakeState(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class _FakeLogger:
    def __init__(self):
        self.entries = []

    def info(self, *a, **kw):
        self.entries.append(("info", a))

    def warning(self, *a, **kw):
        self.entries.append(("warning", a))

    def error(self, *a, **kw):
        self.entries.append(("error", a))

    def debug(self, *a, **kw):
        self.entries.append(("debug", a))


class _FakeVCS:
    def __init__(self, *, is_gitlab=False, existing=None, new_url=None,
                 raise_on=None):
        self.is_gitlab = is_gitlab
        self.existing = existing
        self.new_url = new_url
        self.raise_on = raise_on or set()
        self.comments = []

    def check_pr_exists(self, branch_name):
        if "check_pr_exists" in self.raise_on:
            raise RuntimeError("check raised")
        return self.existing

    def create_pr(self, title, body, head):
        if "create_pr" in self.raise_on:
            raise RuntimeError("create raised")
        return self.new_url

    def comment_on_issue(self, issue_number, body):
        self.comments.append((issue_number, body))


def test_finalize_no_branch_name_logs_and_returns():
    log = _FakeLogger()
    vcs = _FakeVCS()
    git_ops.finalize_git_operations(_FakeState(), log, vcs=vcs)
    levels = [e[0] for e in log.entries]
    assert "warning" in levels


def test_finalize_push_failure_comments_on_issue():
    log = _FakeLogger()
    vcs = _FakeVCS()
    state = _FakeState(branch_name="feat-1", issue_number="42",
                       run_id="run-1")
    with _patch_subprocess(lambda args, **kw: _FakeProc(
            stderr="rejected", returncode=1)):
        git_ops.finalize_git_operations(state, log, vcs=vcs)
    assert vcs.comments
    issue, body = vcs.comments[0]
    assert issue == 42
    assert "Push failed" in body
    assert "rejected" in body


def test_finalize_existing_pr_path():
    log = _FakeLogger()
    vcs = _FakeVCS(existing="https://github.com/o/r/pull/9")
    state = _FakeState(branch_name="feat-1", issue_number="3",
                       run_id="run-x")
    with _patch_subprocess(lambda args, **kw: _FakeProc()):
        git_ops.finalize_git_operations(state, log, vcs=vcs)
    assert vcs.comments
    assert "Updated existing PR" in vcs.comments[0][1]
    assert "/pull/9" in vcs.comments[0][1]


def test_finalize_new_pr_creation_comments_with_long_label():
    log = _FakeLogger()
    vcs = _FakeVCS(existing=None,
                   new_url="https://github.com/o/r/pull/22")
    state = _FakeState(branch_name="feat-2", issue_number="5",
                       run_id="run-y")
    with _patch_subprocess(lambda args, **kw: _FakeProc()):
        git_ops.finalize_git_operations(state, log, vcs=vcs)
    assert vcs.comments
    assert "Created Pull Request" in vcs.comments[0][1]
    assert "/pull/22" in vcs.comments[0][1]


def test_finalize_gitlab_label_swap():
    log = _FakeLogger()
    vcs = _FakeVCS(is_gitlab=True, existing=None,
                   new_url="https://gitlab.example/o/r/-/merge_requests/3")
    state = _FakeState(branch_name="feat-3", issue_number="1",
                       run_id="run-z")
    with _patch_subprocess(lambda args, **kw: _FakeProc()):
        git_ops.finalize_git_operations(state, log, vcs=vcs)
    assert vcs.comments
    assert "Created Merge Request" in vcs.comments[0][1]


def test_finalize_create_pr_returns_none_logs_error():
    log = _FakeLogger()
    vcs = _FakeVCS(existing=None, new_url=None)
    state = _FakeState(branch_name="feat-4", issue_number="9",
                       run_id="run-w")
    with _patch_subprocess(lambda args, **kw: _FakeProc()):
        git_ops.finalize_git_operations(state, log, vcs=vcs)
    assert any(e[0] == "error" for e in log.entries)


def test_finalize_swallows_vcs_init_failure(monkeypatch):
    log = _FakeLogger()
    state = _FakeState(branch_name="feat-x", run_id="r")

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("nope")

    import sys as _sys
    fake_mod = type("M", (), {"VCS": _Boom})
    monkeypatch.setitem(_sys.modules, "tools.ci.modules.vcs", fake_mod)
    git_ops.finalize_git_operations(state, log)  # vcs left as None
    assert any(e[0] == "error" for e in log.entries)
