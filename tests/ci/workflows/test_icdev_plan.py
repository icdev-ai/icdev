# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_plan.py."""
from __future__ import annotations

import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_plan  # noqa: E402


class _Resp:
    def __init__(self, success=True, output=""):
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
    def __init__(self, issue_data=None, is_gitlab=False):
        self.comments = []
        self.is_gitlab = is_gitlab
        self._issue_data = issue_data or {"title": "T", "body": "x"}

    def fetch_issue(self, n):
        return self._issue_data

    def comment_on_issue(self, issue, body):
        self.comments.append((issue, body))

    def check_pr_exists(self, branch_name):
        return None

    def create_pr(self, **_kw):
        return None


def _logger():
    return logging.getLogger("t")


def _wire(monkeypatch, fake_state, fake_vcs, *, plan_path=None,
          plan_ok=True, classify=("/feature", None),
          branched=("feat-1", None), commit=("commit-msg", None),
          branch_ok=True, commit_ok=True, env_ok=True):
    monkeypatch.setattr(icdev_plan, "setup_logger",
                        lambda *a, **k: _logger())
    monkeypatch.setattr(
        icdev_plan.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_plan, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(
        icdev_plan, "check_env_vars",
        lambda log: None if env_ok else (_ for _ in ()).throw(SystemExit(1)),
    )
    monkeypatch.setattr(
        icdev_plan, "ensure_run_id", lambda issue, rid: rid or "rid",
    )
    monkeypatch.setattr(
        icdev_plan, "classify_issue", lambda *a, **k: classify,
    )
    monkeypatch.setattr(
        icdev_plan, "generate_branch_name", lambda *a, **k: branched,
    )
    monkeypatch.setattr(
        icdev_plan, "create_branch",
        lambda b: (branch_ok, None if branch_ok else "denied"),
    )
    monkeypatch.setattr(
        icdev_plan, "build_plan",
        lambda *a, **k: _Resp(success=plan_ok, output=str(plan_path or "")),
    )
    monkeypatch.setattr(
        icdev_plan, "create_commit", lambda *a, **k: commit,
    )
    monkeypatch.setattr(
        icdev_plan, "commit_changes",
        lambda msg, paths=None: (commit_ok, None if commit_ok else "denied"),
    )
    monkeypatch.setattr(
        icdev_plan, "finalize_git_operations", lambda *a, **k: None,
    )


# ────────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────────


def test_missing_args_returns_one(capsys):
    rc = icdev_plan.main(["icdev_plan.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_happy_path_returns_zero(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    state = _State()
    vcs = _VCS()
    _wire(monkeypatch, state, vcs, plan_path=str(plan))
    rc = icdev_plan.main(["icdev_plan.py", "9"])
    assert rc == 0
    bodies = [b for _, b in vcs.comments]
    assert any("Starting planning" in b for b in bodies)
    assert any("Planning phase completed" in b for b in bodies)


def test_classification_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS()
    _wire(monkeypatch, state, vcs,
          classify=(None, "agent crashed"))
    rc = icdev_plan.main(["icdev_plan.py", "9"])
    assert rc == 1


def test_branch_create_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS()
    _wire(monkeypatch, state, vcs, branch_ok=False)
    rc = icdev_plan.main(["icdev_plan.py", "9"])
    assert rc == 1


def test_plan_failure_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS()
    _wire(monkeypatch, state, vcs, plan_ok=False)
    rc = icdev_plan.main(["icdev_plan.py", "9"])
    assert rc == 1


def test_plan_file_missing_returns_one(monkeypatch, tmp_path):
    state = _State()
    vcs = _VCS()
    _wire(monkeypatch, state, vcs, plan_path=str(tmp_path / "absent.md"))
    rc = icdev_plan.main(["icdev_plan.py", "9"])
    assert rc == 1


def test_commit_failure_returns_one(monkeypatch, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan")
    state = _State()
    vcs = _VCS()
    _wire(monkeypatch, state, vcs, plan_path=str(plan), commit_ok=False)
    rc = icdev_plan.main(["icdev_plan.py", "9"])
    assert rc == 1


# ────────────────────────────────────────────────────────────────────────────
# check_env_vars
# ────────────────────────────────────────────────────────────────────────────


def test_check_env_vars_returns_when_claude_cli_present(monkeypatch):
    monkeypatch.setattr(
        icdev_plan.shutil, "which", lambda name: "/usr/local/bin/claude",
    )
    icdev_plan.check_env_vars(_logger())  # must not raise


def test_check_env_vars_uses_llm_router_when_no_cli(monkeypatch):
    monkeypatch.setattr(icdev_plan.shutil, "which", lambda name: None)

    class _FakeProvider:
        provider_name = "ollama"

    class _Router:
        def get_provider_for_function(self, fn):
            return (_FakeProvider(), "qwen3:1.7b", {})

    fake_module = type("M", (), {"LLMRouter": _Router})
    sys.modules["tools.llm.router"] = fake_module
    try:
        icdev_plan.check_env_vars(_logger())  # must not raise
    finally:
        del sys.modules["tools.llm.router"]
