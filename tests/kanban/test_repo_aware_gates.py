# CUI // SP-CTI
"""The done-gate and the PR flow must ask the TASK'S repo, not ICDev (ked-core-03).

``_branch_has_unmerged_commits`` (done-gate), ``_has_open_pr`` (respawn guard),
the merge path and ``_push_main`` all used to run git/gh with ``cwd=BASE_DIR``.
For an external task that asks ICDev whether COMPASS's work landed — the answer
is always no, which is the churn ``repo_registry`` was written to stop.

Guarantees under test:
  1. ``_task_repo_root`` / ``_task_base_branch`` resolve per task: ICDev by
     default, the registered root+base for an external task, None when an
     external repo's root env var is unset.
  2. The done-gate compares against the TARGET repo's ``origin/<base>`` with
     that repo as cwd — and never runs git at all when the root is unset.
  3. ``_has_open_pr`` runs gh in the target repo (gh infers the repo from the
     cwd's remote), so an external task's open PR is actually seen.
  4. ``_push_main`` pushes to the base branch it is handed, not ICDev's.
  5. The PR flow opens the PR in the target repo against ITS base branch.
  6. An external task with no configured root is never merged into ICDev.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as kb  # noqa: E402

_REGISTRY = """
repos:
  compass:
    base_branch: trunk
    root_env: TEST_KED_COMPASS_ROOT
  idea_lab:
    base_branch: main
    root_env: TEST_KED_IDEA_LAB_ROOT
prefixes:
  prem-cpmp: compass
  prem-ideal: idea_lab
"""


class _Fake:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def compass_root(tmp_path, monkeypatch):
    """Registry with compass configured (root set) and idea_lab unconfigured."""
    cfg = tmp_path / "kanban_external_repos.yaml"
    cfg.write_text(_REGISTRY, encoding="utf-8")
    root = tmp_path / "compass"
    root.mkdir()
    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", str(cfg))
    monkeypatch.setenv("TEST_KED_COMPASS_ROOT", str(root))
    monkeypatch.delenv("TEST_KED_IDEA_LAB_ROOT", raising=False)
    return str(root)


class _Recorder:
    """Records the (argv, cwd) of every subprocess.run call."""

    def __init__(self, responder=None):
        self.calls: list[tuple[list, str | None]] = []
        self._responder = responder or (lambda argv: _Fake(returncode=0))

    def __call__(self, cmd, *a, **kw):
        argv = cmd if isinstance(cmd, list) else [cmd]
        self.calls.append((argv, kw.get("cwd")))
        return self._responder(argv)

    def cwds(self) -> set:
        return {c for _, c in self.calls}


# ── 1. Resolution ────────────────────────────────────────────────────────────

def test_icdev_task_resolves_to_base_dir(compass_root):
    assert kb._task_repo_root("ked-core-03") == str(kb.BASE_DIR)


def test_external_task_resolves_to_its_own_root_and_base(compass_root):
    assert kb._task_repo_root("prem-cpmp-07") == compass_root
    assert kb._task_base_branch("prem-cpmp-07") == "trunk"


def test_external_task_without_configured_root_resolves_to_none(compass_root):
    assert kb._task_repo_root("prem-ideal-02") is None


# ── 2. Done-gate ─────────────────────────────────────────────────────────────

def test_done_gate_asks_the_target_repo(compass_root, monkeypatch):
    rec = _Recorder(lambda argv: _Fake(returncode=0, stdout="c1 shipped\n"
                                       if "log" in argv else ""))
    monkeypatch.setattr(subprocess, "run", rec)

    assert kb._branch_has_unmerged_commits("prem-cpmp-07") is True

    assert rec.cwds() == {compass_root}, "git ran outside the task's repo"
    log_argv = next(a for a, _ in rec.calls if "log" in a)
    assert "origin/trunk..kanban/prem-cpmp-07" in log_argv


def test_done_gate_fails_open_when_external_root_unconfigured(compass_root, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)

    # Fail-open (False = don't block done) AND never ask ICDev's git about it.
    assert kb._branch_has_unmerged_commits("prem-ideal-02") is False
    assert rec.calls == []


# ── 3. Respawn guard ─────────────────────────────────────────────────────────

def test_has_open_pr_runs_gh_in_the_target_repo(compass_root, monkeypatch):
    rec = _Recorder(lambda argv: _Fake(returncode=0, stdout='[{"number": 12}]'))
    monkeypatch.setattr(subprocess, "run", rec)

    assert kb._has_open_pr("prem-cpmp-07") is True
    assert rec.cwds() == {compass_root}


def test_has_open_pr_false_when_external_root_unconfigured(compass_root, monkeypatch):
    rec = _Recorder(lambda argv: _Fake(returncode=0, stdout='[{"number": 12}]'))
    monkeypatch.setattr(subprocess, "run", rec)

    assert kb._has_open_pr("prem-ideal-02") is False
    assert rec.calls == []


# ── 4. Push targets the task's base branch ───────────────────────────────────

def test_push_main_pushes_to_the_supplied_base_branch(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)

    assert kb._push_main(cwd="/somewhere", default_branch="trunk") is True
    argv, cwd = rec.calls[0]
    assert argv == ["git", "push", "origin", "HEAD:trunk"]
    assert cwd == "/somewhere"


def test_push_main_defaults_to_icdev_branch(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")

    assert kb._push_main(cwd=".") is True
    assert rec.calls[0][0] == ["git", "push", "origin", "HEAD:main"]


# ── 5. PR flow ───────────────────────────────────────────────────────────────

def test_pr_is_opened_in_the_target_repo_against_its_base(compass_root, monkeypatch):
    def respond(argv):
        if "log" in argv:
            return _Fake(returncode=0, stdout="c1 shipped\n")
        if argv[0] == "gh":
            return _Fake(returncode=0, stdout="https://github.com/acme/compass/pull/9\n")
        return _Fake(returncode=0)

    rec = _Recorder(respond)
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(kb, "_ensure_pr_base", lambda ref, tid: None)
    monkeypatch.setattr(kb, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))

    url = kb._push_branch_and_open_pr("prem-cpmp-07", "c1 shipped")
    assert url == "https://github.com/acme/compass/pull/9"

    assert rec.cwds() == {compass_root}, "push/gh ran outside the task's repo"
    create = next(a for a, _ in rec.calls if a[:3] == ["gh", "pr", "create"])
    assert create[create.index("--base") + 1] == "trunk"
    assert create[create.index("--head") + 1] == "kanban/prem-cpmp-07"


def test_no_pr_opened_when_external_root_unconfigured(compass_root, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)

    assert kb._push_branch_and_open_pr("prem-ideal-02", "work") is None
    assert rec.calls == []


# ── 6. Merge path ────────────────────────────────────────────────────────────

def test_merge_refuses_external_task_with_unconfigured_root(compass_root, monkeypatch):
    rec = _Recorder(lambda argv: _Fake(returncode=0, stdout="c1 shipped\n"))
    monkeypatch.setattr(subprocess, "run", rec)

    # Fail-CLOSED: an unconfigured external task must never be merged into ICDev.
    assert kb._merge_worktree_to_main("prem-ideal-02") is False
    assert rec.calls == []


def test_merge_runs_in_the_target_repo(compass_root, monkeypatch):
    def respond(argv):
        if "log" in argv:
            return _Fake(returncode=0, stdout="c1 shipped\n")
        if "rev-parse" in argv:
            return _Fake(returncode=0, stdout="deadbeef")
        return _Fake(returncode=0)

    rec = _Recorder(respond)
    monkeypatch.setattr(subprocess, "run", rec)

    assert kb._merge_worktree_to_main("prem-cpmp-07") is True

    # Everything runs either in compass's root or in the temp merge worktree.
    merge_wt = str(kb.WORKTREE_BASE / ".merge-prem-cpmp-07")
    assert rec.cwds() <= {compass_root, merge_wt}
    assert str(kb.BASE_DIR) not in rec.cwds()
    push = next(a for a, _ in rec.calls if "push" in a)
    assert push == ["git", "push", "origin", "HEAD:trunk"]
