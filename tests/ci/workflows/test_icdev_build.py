# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_build.py."""
from __future__ import annotations

import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_build  # noqa: E402


class _FakeResponse:
    def __init__(self, success=True, output="ok"):
        self.success = success
        self.output = output


class _FakeState:
    def __init__(self, **kw):
        self._d = dict(kw)

    def get(self, k, default=None):
        return self._d.get(k, default)

    def save(self, *_a, **_kw):
        pass


class _FakeVCS:
    def __init__(self):
        self.comments = []
        self.is_gitlab = False
        self.fetched = []

    def comment_on_issue(self, issue, body):
        self.comments.append((issue, body))

    def fetch_issue(self, issue):
        self.fetched.append(issue)
        return {"title": "demo", "number": issue}

    def check_pr_exists(self, branch_name):
        return None

    def create_pr(self, **_kw):
        return None


def _logger():
    return logging.getLogger("t")


def _wire(monkeypatch, fake_state, fake_vcs, *, branch_ok=True,
          plan_path=None, agent_success=True, agent_output="ok",
          commit_ok=True):
    monkeypatch.setattr(icdev_build, "setup_logger",
                        lambda *a, **k: _logger())
    monkeypatch.setattr(
        icdev_build.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_build, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(
        icdev_build, "create_branch",
        lambda b: (branch_ok, None if branch_ok else "no permission"),
    )
    monkeypatch.setattr(
        icdev_build, "implement_plan",
        lambda *a, **k: _FakeResponse(success=agent_success, output=agent_output),
    )
    monkeypatch.setattr(
        icdev_build, "create_commit",
        lambda *a, **k: ("commit-msg", None),
    )
    monkeypatch.setattr(
        icdev_build, "commit_changes",
        lambda msg, paths=None: (commit_ok, None if commit_ok else "denied"),
    )
    monkeypatch.setattr(
        icdev_build, "finalize_git_operations", lambda *a, **k: None
    )


def test_missing_args_returns_one(capsys):
    rc = icdev_build.main(["icdev_build.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_missing_branch_returns_one(monkeypatch):
    fake_state = _FakeState()
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs)
    rc = icdev_build.main(["icdev_build.py", "1", "run-x"])
    assert rc == 1


def test_branch_checkout_failure_returns_one(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    fake_state = _FakeState(branch_name="feat-1", plan_file=str(plan))
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs, branch_ok=False)
    rc = icdev_build.main(["icdev_build.py", "1", "run-x"])
    assert rc == 1


def test_missing_plan_file_returns_one(monkeypatch, tmp_path):
    fake_state = _FakeState(
        branch_name="feat-1", plan_file=str(tmp_path / "absent.md"),
    )
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs)
    rc = icdev_build.main(["icdev_build.py", "1", "run-x"])
    assert rc == 1


def test_agent_failure_posts_failure_comment(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    fake_state = _FakeState(branch_name="feat-1", plan_file=str(plan))
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs, agent_success=False,
          agent_output="bug bug bug")
    rc = icdev_build.main(["icdev_build.py", "9", "run-x"])
    assert rc == 1
    bodies = [b for _, b in fake_vcs.comments]
    assert any("Implementation failed" in b for b in bodies)


def test_happy_path_commits_and_finalises(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    fake_state = _FakeState(branch_name="feat-1", plan_file=str(plan),
                            issue_class="/feature")
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs)
    finalize_calls = []
    monkeypatch.setattr(
        icdev_build, "finalize_git_operations",
        lambda *a, **k: finalize_calls.append(True),
    )
    rc = icdev_build.main(["icdev_build.py", "9", "run-x"])
    assert rc == 0
    bodies = [b for _, b in fake_vcs.comments]
    assert any("Starting implementation" in b for b in bodies)
    assert any("committed and pushed" in b for b in bodies)
    assert finalize_calls


def test_create_commit_error_falls_back_to_default(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    fake_state = _FakeState(branch_name="feat-1", plan_file=str(plan))
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs)
    monkeypatch.setattr(
        icdev_build, "create_commit",
        lambda *a, **k: (None, "agent timeout"),
    )
    captured = []
    monkeypatch.setattr(
        icdev_build, "commit_changes",
        lambda msg, paths=None: (captured.append(msg), (True, None))[1],
    )
    rc = icdev_build.main(["icdev_build.py", "33", "run-x"])
    assert rc == 0
    assert captured
    assert "implement plan for issue #33" in captured[0]


def test_commit_failure_returns_one(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    fake_state = _FakeState(branch_name="feat-1", plan_file=str(plan))
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs, commit_ok=False)
    rc = icdev_build.main(["icdev_build.py", "1", "run-x"])
    assert rc == 1


def test_safe_fetch_issue_swallows_exceptions():
    class _Boom:
        def fetch_issue(self, n):
            raise RuntimeError("503")

    out = icdev_build._safe_fetch_issue(_Boom(), 5)
    assert out == {}


def test_safe_fetch_issue_returns_empty_when_no_issue():
    out = icdev_build._safe_fetch_issue(_FakeVCS(), None)
    assert out == {}
