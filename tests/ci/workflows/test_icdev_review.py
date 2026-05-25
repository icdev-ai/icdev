# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_review.py."""
from __future__ import annotations

import logging
import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_review  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Constants exposed for callers
# ────────────────────────────────────────────────────────────────────────────


def test_module_constants_exposed():
    assert icdev_review.AGENT_REVIEWER == "icdev_reviewer"
    assert icdev_review.MAX_REVIEW_RETRY == 3
    assert icdev_review.OUTPUT_PREVIEW_CHARS == 2000


# ────────────────────────────────────────────────────────────────────────────
# run_review
# ────────────────────────────────────────────────────────────────────────────


class _FakeAgentResponse:
    def __init__(self, success=True, output="ok", session_id="s1"):
        self.success = success
        self.output = output
        self.session_id = session_id


def test_run_review_returns_canonical_dict():
    with patch.object(
        icdev_review, "execute_template",
        return_value=_FakeAgentResponse(),
    ):
        result = icdev_review.run_review(
            "plan.md", "run-1", logging.getLogger("t"),
        )
    assert result == {"success": True, "output": "ok", "session_id": "s1"}


def test_run_review_handles_dict_response_shape():
    fake = type("R", (), {})()
    fake.success = False
    fake.output = "broken"
    fake.session_id = None
    with patch.object(icdev_review, "execute_template", return_value=fake):
        out = icdev_review.run_review(
            "plan.md", "run-2", logging.getLogger("t"),
        )
    assert out["success"] is False
    assert out["output"] == "broken"


# ────────────────────────────────────────────────────────────────────────────
# main()
# ────────────────────────────────────────────────────────────────────────────


class _FakeState:
    def __init__(self, plan_file=None):
        self._d = {}
        if plan_file is not None:
            self._d["plan_file"] = plan_file

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


def _make_logger():
    return logging.getLogger("t")


def test_main_missing_args_returns_one(capsys):
    rc = icdev_review.main(["icdev_review.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_main_missing_plan_file_exits_one(monkeypatch, tmp_path):
    fake_state = _FakeState(plan_file=str(tmp_path / "absent.md"))
    fake_vcs = _FakeVCS()

    monkeypatch.setattr(icdev_review, "setup_logger",
                        lambda *a, **k: _make_logger())
    monkeypatch.setattr(
        icdev_review.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_review, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(icdev_review, "execute_template",
                        lambda *a, **k: _FakeAgentResponse())

    rc = icdev_review.main(["icdev_review.py", "42", "run-x"])
    assert rc == 1
    # Should have posted the "no plan file" comment
    assert any("plan file" in body.lower() for _, body in fake_vcs.comments)


def test_main_success_path_comments_and_commits(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan", encoding="utf-8")
    fake_state = _FakeState(plan_file=str(plan))
    fake_vcs = _FakeVCS()
    commit_calls = []

    monkeypatch.setattr(icdev_review, "setup_logger",
                        lambda *a, **k: _make_logger())
    monkeypatch.setattr(
        icdev_review.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_review, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(
        icdev_review, "execute_template",
        lambda req: _FakeAgentResponse(success=True, output="LGTM"),
    )

    def fake_commit(msg, paths=None):
        commit_calls.append(msg)
        return True, None

    monkeypatch.setattr(icdev_review, "commit_changes", fake_commit)

    finalize_called = []
    monkeypatch.setattr(
        icdev_review, "finalize_git_operations",
        lambda *a, **k: finalize_called.append(True),
    )

    rc = icdev_review.main(["icdev_review.py", "9", "run-y"])
    assert rc == 0
    # Two comments: "Starting" and "Code Review Complete"
    bodies = [b for _, b in fake_vcs.comments]
    assert any("Starting" in b for b in bodies)
    assert any("Code Review Complete" in b for b in bodies)
    assert commit_calls
    assert finalize_called


def test_main_failure_path_uses_issues_header(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan", encoding="utf-8")
    fake_state = _FakeState(plan_file=str(plan))
    fake_vcs = _FakeVCS()

    monkeypatch.setattr(icdev_review, "setup_logger",
                        lambda *a, **k: _make_logger())
    monkeypatch.setattr(
        icdev_review.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_review, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(
        icdev_review, "execute_template",
        lambda req: _FakeAgentResponse(success=False, output="bad bug"),
    )
    monkeypatch.setattr(icdev_review, "commit_changes",
                        lambda *a, **k: (True, None))
    monkeypatch.setattr(icdev_review, "finalize_git_operations",
                        lambda *a, **k: None)

    rc = icdev_review.main(["icdev_review.py", "5", "run-z"])
    assert rc == 0
    bodies = [b for _, b in fake_vcs.comments]
    assert any("Code Review Issues" in b for b in bodies)


def test_main_non_numeric_issue_does_not_crash(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan", encoding="utf-8")
    fake_state = _FakeState(plan_file=str(plan))
    fake_vcs = _FakeVCS()

    monkeypatch.setattr(icdev_review, "setup_logger",
                        lambda *a, **k: _make_logger())
    monkeypatch.setattr(
        icdev_review.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_review, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(icdev_review, "execute_template",
                        lambda req: _FakeAgentResponse())
    monkeypatch.setattr(icdev_review, "commit_changes",
                        lambda *a, **k: (True, None))
    monkeypatch.setattr(icdev_review, "finalize_git_operations",
                        lambda *a, **k: None)

    rc = icdev_review.main(["icdev_review.py", "not-a-number", "run-bad"])
    # Non-numeric issue is logged but doesn't bring the workflow down
    assert rc == 0
    # And no comments were attempted (issue_int is None)
    assert fake_vcs.comments == []
