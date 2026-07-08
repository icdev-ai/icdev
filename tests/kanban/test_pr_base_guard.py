# CUI // SP-CTI
"""Tests for the kanban PR-flow base-branch guard (kanban-pr-base-guard-01).

Incident 2026-07-08: PR #114 (ground-dic-05) was opened with base
feat/rfi-six-parts instead of main and auto-merged there, stranding the
change off-main. The reflex now verifies the PR base after `gh pr create`
and retargets it to the default branch via `gh pr edit`.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as kb  # noqa: E402

PR_URL = "https://github.com/o/r/pull/114"


def _gh_view_result(base_ref, rc=0):
    return SimpleNamespace(
        returncode=rc,
        stdout=json.dumps({"baseRefName": base_ref, "url": PR_URL}) if rc == 0 else "",
        stderr="no pull requests found" if rc else "",
    )


def _fake_run_factory(calls, base_ref, view_rc=0, edit_rc=0):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return _gh_view_result(base_ref, view_rc)
        if cmd[:3] == ["gh", "pr", "edit"]:
            return SimpleNamespace(returncode=edit_rc, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")
    return fake_run


def _edit_calls(calls):
    return [c for c in calls if c[:3] == ["gh", "pr", "edit"]]


# ────────────────────────────────────────────────────────────────────────────
# _ensure_pr_base
# ────────────────────────────────────────────────────────────────────────────


def test_ensure_pr_base_retargets_wrong_base(monkeypatch):
    calls = []
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    monkeypatch.setattr(
        subprocess, "run", _fake_run_factory(calls, "feat/rfi-six-parts")
    )
    url = kb._ensure_pr_base(PR_URL, "task-x")
    assert url == PR_URL
    edits = _edit_calls(calls)
    assert len(edits) == 1
    assert edits[0][-2:] == ["--base", "main"]


def test_ensure_pr_base_noop_on_default_base(monkeypatch):
    calls = []
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(calls, "main"))
    url = kb._ensure_pr_base("kanban/task-x", "task-x")
    assert url == PR_URL
    assert _edit_calls(calls) == []


def test_ensure_pr_base_returns_none_when_no_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    monkeypatch.setattr(
        subprocess, "run", _fake_run_factory(calls, "", view_rc=1)
    )
    assert kb._ensure_pr_base("kanban/task-x", "task-x") is None
    assert _edit_calls(calls) == []


# ────────────────────────────────────────────────────────────────────────────
# _push_branch_and_open_pr wiring
# ────────────────────────────────────────────────────────────────────────────


def _no_db():
    raise RuntimeError("no db in test")


def _push_flow_run_factory(calls, create_rc, create_stdout, create_stderr,
                           view_base):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "log"]:
            return SimpleNamespace(returncode=0, stdout="abc123 change\n", stderr="")
        if cmd[:2] == ["git", "push"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["gh", "pr", "create"]:
            return SimpleNamespace(
                returncode=create_rc, stdout=create_stdout, stderr=create_stderr
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _gh_view_result(view_base)
        if cmd[:3] == ["gh", "pr", "edit"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")
    return fake_run


def test_push_flow_retargets_after_create(monkeypatch):
    calls = []
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    monkeypatch.setattr(kb, "get_connection", _no_db)
    monkeypatch.setattr(
        subprocess, "run",
        _push_flow_run_factory(calls, 0, PR_URL + "\n", "", "feat/wrong"),
    )
    assert kb._push_branch_and_open_pr("task-x", "summary") == PR_URL
    edits = _edit_calls(calls)
    assert len(edits) == 1
    assert edits[0][-2:] == ["--base", "main"]


def test_push_flow_reuses_existing_pr_and_retargets(monkeypatch):
    # gh pr create fails because the task agent already opened a PR
    # (with a wrong base) — the guard resolves it by head branch,
    # retargets it, and returns its URL.
    calls = []
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    monkeypatch.setattr(kb, "get_connection", _no_db)
    stderr = (
        'a pull request for branch "kanban/task-x" into branch '
        f'"feat/rfi-six-parts" already exists:\n{PR_URL}'
    )
    monkeypatch.setattr(
        subprocess, "run",
        _push_flow_run_factory(calls, 1, "", stderr, "feat/rfi-six-parts"),
    )
    assert kb._push_branch_and_open_pr("task-x", "summary") == PR_URL
    edits = _edit_calls(calls)
    assert len(edits) == 1
    assert edits[0][-2:] == ["--base", "main"]
