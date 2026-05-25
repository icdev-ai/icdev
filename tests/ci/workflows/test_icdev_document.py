# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_document.py."""
from __future__ import annotations

import logging
import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_document  # noqa: E402


class _FakeResponse:
    def __init__(self, success=True, output="docs/feature.md"):
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

    def comment_on_issue(self, issue, body):
        self.comments.append((issue, body))

    def check_pr_exists(self, branch_name):
        return None

    def create_pr(self, **_kw):
        return None


def _logger():
    return logging.getLogger("t")


def _wire(monkeypatch, fake_state, fake_vcs, *, branch_ok=True,
          changes=True, agent_success=True, agent_output="docs/x.md"):
    monkeypatch.setattr(icdev_document, "setup_logger",
                        lambda *a, **k: _logger())
    monkeypatch.setattr(
        icdev_document.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_document, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(
        icdev_document, "create_branch",
        lambda b: (branch_ok, None if branch_ok else "no permission"),
    )
    monkeypatch.setattr(
        icdev_document, "check_for_changes", lambda log: changes,
    )
    monkeypatch.setattr(
        icdev_document, "execute_template",
        lambda req: _FakeResponse(success=agent_success, output=agent_output),
    )
    monkeypatch.setattr(
        icdev_document, "commit_changes", lambda msg, paths=None: (True, None),
    )
    monkeypatch.setattr(
        icdev_document, "finalize_git_operations", lambda *a, **k: None,
    )


def test_missing_args_returns_one(capsys):
    rc = icdev_document.main(["icdev_document.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_missing_branch_returns_one(monkeypatch):
    fake_state = _FakeState()
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs)
    rc = icdev_document.main(["icdev_document.py", "1", "run-x"])
    assert rc == 1


def test_no_changes_skips_and_returns_zero(monkeypatch):
    fake_state = _FakeState(branch_name="feat-1")
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs, changes=False)
    rc = icdev_document.main(["icdev_document.py", "9", "run-x"])
    assert rc == 0
    bodies = [b for _, b in fake_vcs.comments]
    assert any("No changes" in b for b in bodies)


def test_agent_success_path_creates_doc_comment(monkeypatch):
    fake_state = _FakeState(branch_name="feat-1", plan_file="plan.md")
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs)
    rc = icdev_document.main(["icdev_document.py", "9", "run-x"])
    assert rc == 0
    bodies = [b for _, b in fake_vcs.comments]
    assert any("Documentation created" in b for b in bodies)


def test_agent_failure_returns_one(monkeypatch):
    fake_state = _FakeState(branch_name="feat-1")
    fake_vcs = _FakeVCS()
    _wire(monkeypatch, fake_state, fake_vcs, agent_success=False,
          agent_output="LLM 503")
    rc = icdev_document.main(["icdev_document.py", "9", "run-x"])
    assert rc == 1
    bodies = [b for _, b in fake_vcs.comments]
    assert any("Documentation generation failed" in b for b in bodies)


def test_check_for_changes_uses_git_diff_origin_main():
    seen = []

    class _Proc:
        stdout = " 2 files changed\n"
        stderr = ""
        returncode = 0

    def fake_run(args, **kw):
        seen.append(args)
        return _Proc()

    with patch.object(icdev_document.subprocess, "run", side_effect=fake_run):
        assert icdev_document.check_for_changes(_logger()) is True
    assert "git" in seen[0][0]
    assert "diff" in seen[0]
    assert "origin/main" in seen[0]


def test_check_for_changes_returns_false_on_clean_tree():
    class _Proc:
        stdout = ""
        stderr = ""
        returncode = 0

    with patch.object(icdev_document.subprocess, "run", return_value=_Proc()):
        assert icdev_document.check_for_changes(_logger()) is False


def test_check_for_changes_returns_true_on_subprocess_error():
    def boom(*_a, **_kw):
        raise OSError("git not on path")

    with patch.object(icdev_document.subprocess, "run", side_effect=boom):
        assert icdev_document.check_for_changes(_logger()) is True
