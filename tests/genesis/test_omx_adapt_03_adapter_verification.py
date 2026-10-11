# CUI // SP-CTI
"""omx-adapt-03: an opencode/Pi dispatch that DID commit is verified, not re-dispatched.

MEASURED 2026-10-11 on the Omarchy box (external scratch repo, opencode_cli):
omx-smoke-02's worker committed calc.py + test_calc.py and pushed them to
``origin/kanban/omx-smoke-02`` AND ``origin/main``; the scratch repo's local main
sat 2 behind origin. The verifier said "No evidence of file changes in output"
and the runner re-dispatched it. Two defects:

* git-first: arm 1 excludes ``origin/<base>`` (so landed work vanishes) and arm 3
  looks only at the LOCAL HEAD -- the push-to-main landing matched neither.
* the output fallback read the log, which for opencode/Pi holds only the final
  sentence; the run's file edits live in the JSONL stream the adapter parses.

Real git, as in test_kpr_watch_25_git_first_without_baseline.py: the defect is
what git is asked, so a mocked git would prove nothing.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.agents.adapter_base import AgentResult
from tools.genesis.reflexes import kanban as K

TASK = "omx-x-03"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )


def _head(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["git", "clone", str(origin), str(work)],
                   capture_output=True, check=True, timeout=60)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "-u", "origin", "main")
    monkeypatch.setattr(K, "_task_repo_root", lambda tid: work)
    monkeypatch.setattr(K, "_task_base_branch", lambda tid: "main")
    monkeypatch.setattr(K, "_work_dir_for", lambda tid: str(work))
    monkeypatch.setattr(K, "_dir_owns_its_repo_root", lambda d: True)
    monkeypatch.setattr(K, "_is_dangerous_task", lambda tid: False)
    monkeypatch.setattr(K, "_dispatch_main_heads", {})
    monkeypatch.setattr(K, "_dispatch_origin_heads", {})
    monkeypatch.setattr(K, "_worktrees", {})
    monkeypatch.setattr(K, "PROMPT_DIR", tmp_path / "kanban")
    return work


def _sibling_lands_then_dispatch(repo: Path, monkeypatch) -> Path:
    """The Omarchy shape: a sibling task landed on origin/main, local main did not move.

    Returns the task's own worktree, cut from origin/main as _create_worktree
    cuts it, while the repo root stays on its (stale) local main.
    """
    _git(repo, "checkout", "-b", "kanban/omx-x-01")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n"
                                  "def sub(a, b):\n    return a - b\n", encoding="utf-8")
    _git(repo, "commit", "-am", "sibling: subtract")
    _git(repo, "push", "origin", "HEAD:main")
    _git(repo, "checkout", "main")  # local main stays behind origin/main
    # Dispatch: baselines recorded exactly as _dispatch_task records them.
    K._dispatch_main_heads[TASK] = _head(repo, "main")
    K._dispatch_origin_heads[TASK] = _head(repo, "origin/main")
    wt = repo.parent / "wt"
    _git(repo, "worktree", "add", "-b", f"kanban/{TASK}", str(wt), "origin/main")
    monkeypatch.setattr(K, "_work_dir_for", lambda tid: str(wt))
    return wt


def test_work_pushed_to_origin_main_verifies_git_first(repo, monkeypatch):
    wt = _sibling_lands_then_dispatch(repo, monkeypatch)
    (wt / "test_calc.py").write_text("def test_mul():\n    pass\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "Add multiply() with test")
    _git(wt, "push", "origin", f"kanban/{TASK}")
    _git(wt, "push", "origin", "HEAD:main")
    assert _head(repo, "main") != _head(repo, "origin/main"), "local main must lag"

    ok, reason = K._run_verify_checks(TASK, "Done.")
    assert ok is True, reason
    assert reason.startswith("Verified (git-first)")
    assert "already on origin/main" in reason
    assert "test_calc.py" in reason


def test_a_branch_with_only_inherited_commits_is_not_evidence(repo, monkeypatch):
    # Cut from origin/main, committed nothing: the sibling's commit is between
    # LOCAL main and the branch, and must not be credited to this task.
    _sibling_lands_then_dispatch(repo, monkeypatch)

    ok, reason = K._git_worktree_has_real_changes(TASK, committed_only=True)
    assert ok is False, reason


def test_landed_arm_needs_the_origin_snapshot(repo, monkeypatch):
    wt = _sibling_lands_then_dispatch(repo, monkeypatch)
    (wt / "test_calc.py").write_text("x\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "work")
    _git(wt, "push", "origin", "HEAD:main")
    K._dispatch_origin_heads.pop(TASK)
    ok, reason = K._git_worktree_has_real_changes(TASK, committed_only=True)
    assert ok is False, reason


# ---------------------------------------------------------------------------
# The output fallback reads the adapter's stream through parse_response
# ---------------------------------------------------------------------------

def _opencode_stream(work: Path, *, status: str = "completed") -> str:
    events = [
        {"type": "step_start", "sessionID": "s1", "part": {}},
        {"type": "tool_use", "sessionID": "s1", "part": {
            "tool": "write", "callID": "c1",
            "state": {"status": status,
                      "input": {"filePath": str(work / "test_calc.py"), "content": "x"},
                      "output": ""}}},
        {"type": "tool_use", "sessionID": "s1", "part": {
            "tool": "edit", "callID": "c2",
            "state": {"status": status,
                      "input": {"filePath": str(work / "calc.py")}, "output": ""}}},
        {"type": "text", "sessionID": "s1", "part": {"text": "Done."}},
        {"type": "step_finish", "sessionID": "s1", "part": {"reason": "stop"}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _pi_stream(work: Path) -> str:
    events = [
        {"type": "session", "id": "p1", "cwd": str(work)},
        {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "write",
         "args": {"path": "test_calc.py", "content": "x"}},
        {"type": "tool_execution_end", "toolCallId": "t1", "toolName": "write",
         "isError": False},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                                            "content": [{"type": "text", "text": "Done."}]}},
        {"type": "agent_settled", "aborted": False},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _record(task_id: str, adapter: str, raw: str) -> None:
    result = AgentResult(task_id=task_id, adapter_name=adapter, completed=True,
                         output="Done.", structured={"raw_output": raw})
    K._write_adapter_stream(K._adapter_stream_path(task_id), adapter, result)


@pytest.mark.parametrize("adapter,stream", [
    ("opencode_cli", _opencode_stream),
    ("pi_cli", _pi_stream),
])
def test_adapter_stream_file_writes_are_evidence(repo, adapter, stream):
    (repo / "test_calc.py").write_text("x\n", encoding="utf-8")
    _record(TASK, adapter, stream(repo))

    writes, paths = K._adapter_file_evidence(TASK, str(repo))
    assert writes >= 1
    assert "test_calc.py" in paths


def test_a_refused_write_is_not_evidence(repo):
    _record(TASK, "opencode_cli", _opencode_stream(repo, status="error"))
    assert K._adapter_file_evidence(TASK, str(repo)) == (0, [])


def test_opencode_output_with_edits_passes_the_output_checks(repo, monkeypatch):
    """No git evidence at all: the stream alone must clear checks 1 and 3.

    Before omx-adapt-03 this failed "Output too short" on opencode's "Done.";
    the check that follows (5: commits on the branch) is what then decides,
    so it is stubbed to report what it saw.
    """
    monkeypatch.setattr(K, "_verify_claimed_files_exist",
                        lambda paths, wd: (len(paths), len(paths), []))
    _record(TASK, "opencode_cli", _opencode_stream(repo))
    ok, reason = K._run_verify_checks(TASK, "Done.")
    assert "Output too short" not in reason
    assert "No evidence of file changes in output" not in reason


def test_a_stale_stream_is_cleared_at_dispatch(repo, monkeypatch, tmp_path):
    """A previous run's stream must never be read as this run's evidence."""
    _record(TASK, "opencode_cli", _opencode_stream(repo))
    assert K._adapter_stream_path(TASK).exists()

    class _Adapter:
        name = "opencode_cli"

    class _Handle:
        def __init__(self, **kw):
            pass

        def start(self, fn):
            pass

    monkeypatch.setattr(K, "_agent_session", lambda *a, **k: object())
    monkeypatch.setattr(K, "_LLMTaskHandle", _Handle)
    monkeypatch.setattr(K, "_record_dispatch_pid", lambda *a, **k: None)
    monkeypatch.setattr(K, "_open_agent_invocation", lambda *a, **k: None)
    monkeypatch.setattr(K, "_close_agent_invocation", lambda *a, **k: None)
    monkeypatch.setattr(K, "_audit_agent_execution", lambda *a, **k: None)
    monkeypatch.setattr(K, "_running", {})
    K._dispatch_via_agent_adapter(_Adapter(), {"id": TASK}, "", "go", str(repo),
                                  tmp_path / "t.log")
    assert not K._adapter_stream_path(TASK).exists()
    assert K._adapter_file_evidence(TASK, str(repo)) == (0, [])


@pytest.mark.parametrize("modname,stream", [
    ("opencode_cli", _opencode_stream),
    ("pi_cli", _pi_stream),
])
def test_adapters_hand_back_their_raw_stream(tmp_path, monkeypatch, modname, stream):
    """The verifier can only parse what invoke() kept."""
    import importlib
    from tools.agents.adapter_base import AgentSession

    mod = importlib.import_module(f"tools.agents.adapters.{modname}")
    raw = stream(tmp_path)

    class _Proc:
        returncode = 0
        stdout = raw
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc())
    guard = tmp_path / "guard.ts"
    monkeypatch.setattr(mod, "install_guard",
                        lambda *a, **k: {"path": str(guard)} if modname == "pi_cli" else guard)
    adapter = mod.ADAPTER
    monkeypatch.setattr(type(adapter), "resolve", lambda self: "harness")
    session = AgentSession(task_id=TASK, prompt="go", working_dir=str(tmp_path))
    result = adapter.invoke(session)
    assert result.structured.get("raw_output") == raw
