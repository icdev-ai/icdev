# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_patch.py."""
from __future__ import annotations

import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_patch  # noqa: E402


class _Resp:
    def __init__(self, success=True, output="patches/p.md"):
        self.success = success
        self.output = output


class _State:
    def __init__(self, **kw):
        self._d = dict(kw)

    def get(self, k, default=None):
        return self._d.get(k, default)

    def update(self, **kw):
        self._d.update(kw)

    def save(self, *_a, **_kw):
        pass


class _VCS:
    def __init__(self, comments=None, issue_data=None, is_gitlab=False):
        self.comments = []
        self._comments_seed = list(comments or [])
        self._issue_data = issue_data or {}
        self.is_gitlab = is_gitlab

    def fetch_issue(self, n):
        return dict(self._issue_data, number=n)

    def fetch_issue_comments(self, n):
        return list(self._comments_seed)

    def comment_on_issue(self, issue, body):
        self.comments.append((issue, body))

    def check_pr_exists(self, branch_name):
        return None

    def create_pr(self, **_kw):
        return None


def _logger():
    return logging.getLogger("t")


def _wire(monkeypatch, state, vcs, *, branch_ok=True, plan_ok=True,
          impl_ok=True, commit_ok=True, classify=("/patch", None),
          generated=("feat-1", None)):
    monkeypatch.setattr(icdev_patch, "setup_logger", lambda *a, **k: _logger())
    monkeypatch.setattr(
        icdev_patch.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: state),
    )
    monkeypatch.setattr(icdev_patch, "VCS", lambda: vcs)
    monkeypatch.setattr(
        icdev_patch, "ensure_run_id", lambda issue, run_id: run_id or "rid-x",
    )
    monkeypatch.setattr(
        icdev_patch, "classify_issue", lambda *a, **k: classify,
    )
    monkeypatch.setattr(
        icdev_patch, "generate_branch_name", lambda *a, **k: generated,
    )
    monkeypatch.setattr(
        icdev_patch, "create_branch",
        lambda b: (branch_ok, None if branch_ok else "denied"),
    )
    monkeypatch.setattr(
        icdev_patch, "execute_template",
        lambda req: _Resp(success=plan_ok, output="patches/p.md"),
    )
    monkeypatch.setattr(
        icdev_patch, "implement_plan",
        lambda *a, **k: _Resp(success=impl_ok, output=""),
    )
    monkeypatch.setattr(
        icdev_patch, "create_commit",
        lambda *a, **k: ("commit-msg", None),
    )
    monkeypatch.setattr(
        icdev_patch, "commit_changes",
        lambda msg, paths=None: (commit_ok, None if commit_ok else "denied"),
    )
    monkeypatch.setattr(
        icdev_patch, "finalize_git_operations", lambda *a, **k: None,
    )


# ────────────────────────────────────────────────────────────────────────────
# get_patch_content
# ────────────────────────────────────────────────────────────────────────────


def test_patch_content_prefers_keyword_in_comments():
    vcs = _VCS(
        comments=[
            {"body": "first comment"},
            {"body": "icdev_patch: fix the typo on line 5"},
            {"body": "third comment"},
        ],
        issue_data={"title": "T", "body": "no keyword"},
    )
    out = icdev_patch.get_patch_content({"title": "T", "body": "x"}, vcs, 9, _logger())
    assert "fix the typo" in out


def test_patch_content_falls_back_to_issue_body():
    vcs = _VCS(comments=[])
    out = icdev_patch.get_patch_content(
        {"title": "Bug", "body": "icdev_patch: do the thing"},
        vcs, 7, _logger(),
    )
    assert "Issue #7" in out
    assert "do the thing" in out


def test_patch_content_full_fallback_when_keyword_absent():
    vcs = _VCS(comments=[])
    out = icdev_patch.get_patch_content(
        {"title": "Bug", "body": "no special keyword here"},
        vcs, 7, _logger(),
    )
    assert "Issue #7" in out
    assert "Bug" in out


def test_patch_content_handles_gitlab_note_field():
    vcs = _VCS(comments=[{"note": "icdev_patch: do x"}])
    out = icdev_patch.get_patch_content({"title": "T"}, vcs, 1, _logger())
    assert "do x" in out


def test_patch_content_swallows_fetch_exception():
    class _Bad:
        def fetch_issue_comments(self, n):
            raise RuntimeError("503")

    out = icdev_patch.get_patch_content(
        {"title": "T", "body": "no kw"}, _Bad(), 9, _logger(),
    )
    assert "Issue #9" in out


# ────────────────────────────────────────────────────────────────────────────
# main()
# ────────────────────────────────────────────────────────────────────────────


def test_missing_args_returns_one(capsys):
    rc = icdev_patch.main(["icdev_patch.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_happy_path_runs_planner_and_implementor(monkeypatch):
    state = _State()
    vcs = _VCS(comments=[], issue_data={"title": "T", "body": "x"})
    _wire(monkeypatch, state, vcs)
    rc = icdev_patch.main(["icdev_patch.py", "9"])
    assert rc == 0
    bodies = [b for _, b in vcs.comments]
    assert any("Starting patch workflow" in b for b in bodies)
    assert any("Patch workflow completed" in b for b in bodies)


def test_planner_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS(issue_data={"title": "T"})
    _wire(monkeypatch, state, vcs, plan_ok=False)
    rc = icdev_patch.main(["icdev_patch.py", "9"])
    assert rc == 1


def test_implementor_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS(issue_data={"title": "T"})
    _wire(monkeypatch, state, vcs, impl_ok=False)
    rc = icdev_patch.main(["icdev_patch.py", "9"])
    assert rc == 1


def test_branch_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS(issue_data={"title": "T"})
    _wire(monkeypatch, state, vcs, branch_ok=False)
    rc = icdev_patch.main(["icdev_patch.py", "9"])
    assert rc == 1


def test_commit_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS(issue_data={"title": "T"})
    _wire(monkeypatch, state, vcs, commit_ok=False)
    rc = icdev_patch.main(["icdev_patch.py", "9"])
    assert rc == 1


def test_branch_already_in_state_skips_classification(monkeypatch):
    state = _State(branch_name="feat-existing")
    vcs = _VCS(issue_data={"title": "T"})
    classify_called = []

    def _classify(*a, **k):
        classify_called.append(True)
        return ("/patch", None)

    _wire(monkeypatch, state, vcs, classify=("/patch", None))
    monkeypatch.setattr(icdev_patch, "classify_issue", _classify)

    rc = icdev_patch.main(["icdev_patch.py", "9"])
    assert rc == 0
    assert classify_called == []
