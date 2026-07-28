# CUI // SP-CTI
"""The CLI's --set-status done must verify the work is on origin.

Root cause of the recurring "board says done but it is not on main" bug
(observed again 2026-07-28 across ~11 DWO tasks): three code paths can write
status='done' and only two verified the merge.

  runner  reflexes/kanban.py::_move_task           -> gated
  API     POST /api/kanban/tasks/<id>/move         -> gated (guard-22)
  CLI     tools/kanban/cli.py --set-status <id> done -> UNGATED (raw UPDATE)

CLAUDE.md instructs dispatched worker sessions to complete their own task with
the CLI, so every worker self-report reached 'done' regardless of merge state.
"""
from __future__ import annotations

import importlib
import subprocess

import pytest

kb = importlib.import_module("tools.genesis.reflexes.kanban")
cli = importlib.import_module("tools.kanban.cli")


class _Fake:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


# ── Hole 1: branch resolution was exact-match on kanban/<task_id> ───────────

@pytest.mark.parametrize("ref", [
    "kanban/dwo-mcp-02-d5-audit",        # descriptive suffix
    "origin/test/dwo-vv-03-d3-trigger-link",  # different prefix entirely
    "docs/dwo-mcp-03-d5-d1-mcp-readme",  # docs/ prefix
])
def test_branches_for_task_finds_non_canonical_names(monkeypatch, tmp_path, ref):
    """Real branch names from the 2026-07-28 run that the exact-match missed."""
    task_id = {
        "kanban/dwo-mcp-02-d5-audit": "dwo-mcp-02-d5",
        "origin/test/dwo-vv-03-d3-trigger-link": "dwo-vv-03-d3",
        "docs/dwo-mcp-03-d5-d1-mcp-readme": "dwo-mcp-03-d5-d1",
    }[ref]
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _Fake(returncode=0, stdout=ref + "\n"))
    assert kb._branches_for_task(task_id, tmp_path) == [ref]


def test_branches_for_task_respects_name_boundaries(monkeypatch, tmp_path):
    """`dwo-mcp-01` must not match `dwo-mcp-01x`."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _Fake(returncode=0, stdout="kanban/dwo-mcp-01x\n"))
    assert kb._branches_for_task("dwo-mcp-01", tmp_path) == []


def test_branches_for_task_fails_open_on_git_error(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Fake(returncode=128))
    assert kb._branches_for_task("t-1", tmp_path) == []


def test_suffixed_branch_now_blocks_done(monkeypatch):
    """End of hole 1: a suffixed branch with unmerged work must refuse done."""
    def fake_run(cmd, *a, **k):
        argv = cmd if isinstance(cmd, list) else [cmd]
        if "for-each-ref" in argv:
            return _Fake(returncode=0, stdout="kanban/t-1-descriptive-suffix\n")
        if "log" in argv:
            return _Fake(returncode=0, stdout="abc123 unmerged work\n")
        return _Fake(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    assert kb._branch_has_unmerged_commits("t-1") is True


# ── Hole 2: the CLI never consulted the gate ───────────────────────────────

def test_cli_refuses_done_when_work_is_unmerged(monkeypatch, capsys):
    monkeypatch.delenv("KANBAN_REQUIRE_MERGE_FOR_DONE", raising=False)
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda t: True)

    rc = cli.cmd_set_status(["t-1"], "done", json_out=False)

    assert rc == 1, "the CLI marked an unmerged task done"
    assert "REFUSED" in capsys.readouterr().err


def test_cli_allows_done_when_work_is_merged(monkeypatch):
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda t: False)
    assert cli._refuses_done("t-1") == ""


def test_cli_refusal_is_all_or_nothing(monkeypatch, capsys):
    """One unmerged task in a batch refuses the whole batch — no partial writes."""
    monkeypatch.setattr(
        kb, "_branch_has_unmerged_commits", lambda t: t == "t-bad")

    rc = cli.cmd_set_status(["t-ok", "t-bad"], "done", json_out=False)

    assert rc == 1
    err = capsys.readouterr().err
    assert "t-bad" in err and "t-ok" not in err


def test_force_done_requires_a_reason(monkeypatch, capsys):
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda t: True)
    rc = cli.cmd_set_status(["t-1"], "done", json_out=False, force_done=True, reason="")
    assert rc == 1
    assert "--reason" in capsys.readouterr().err


def test_env_toggle_disables_the_gate(monkeypatch):
    """KANBAN_REQUIRE_MERGE_FOR_DONE=0 keeps the documented escape hatch."""
    monkeypatch.setenv("KANBAN_REQUIRE_MERGE_FOR_DONE", "0")
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda t: True)
    assert cli._refuses_done("t-1") == ""


def test_gate_fails_open_when_the_primitive_raises(monkeypatch):
    """Unreachable git must never wedge completions."""
    monkeypatch.delenv("KANBAN_REQUIRE_MERGE_FOR_DONE", raising=False)

    def boom(_):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", boom)
    assert cli._refuses_done("t-1") == ""


def test_non_done_statuses_are_never_gated(monkeypatch):
    """Only 'done' is verified — moving a task to backlog/in_progress must not
    consult git at all, or an unreachable origin would block ordinary moves."""
    from tools.kanban.init_db import init_kanban_tables
    init_kanban_tables()

    called = []
    monkeypatch.setattr(cli, "_refuses_done", lambda t: called.append(t) or "blocked")

    rc = cli.cmd_set_status(["no-such-task-xyz"], "in_progress", json_out=True)

    assert rc == 0
    assert called == [], "the merge gate ran for a non-done transition"
