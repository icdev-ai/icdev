# CUI // SP-CTI
"""OPT-61: tests for agent-loop middleware in tools/airgap/hook_compat.py.

Covers check_message_queue, queue_message, safety_net_pr, and
tool_error_middleware. All tests are offline — no real LLM, no real
git remote, no real gh CLI.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.airgap import hook_compat


# ────────────────────────────────────────────────────────────────────────────
# check_message_queue + queue_message (OPT-61 / OPT-62 primitive)
# ────────────────────────────────────────────────────────────────────────────


def test_check_message_queue_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    assert hook_compat.check_message_queue("task-no-file") == []


def test_check_message_queue_drains_file(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    queue_file = tmp_path / "task-abc.jsonl"
    queue_file.write_text(
        '{"role":"user","content":"first"}\n'
        '{"role":"user","content":"second"}\n',
        encoding="utf-8",
    )
    msgs = hook_compat.check_message_queue("task-abc")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "first"
    assert msgs[1]["content"] == "second"
    # Drain semantics — file removed after read
    assert not queue_file.exists()


def test_check_message_queue_handles_malformed_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    queue_file = tmp_path / "task-bad.jsonl"
    queue_file.write_text(
        '{"role":"user","content":"ok"}\n'
        'this is not json\n'
        '{"role":"user","content":"also ok"}\n',
        encoding="utf-8",
    )
    msgs = hook_compat.check_message_queue("task-bad")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "ok"
    assert msgs[1]["content"] == "also ok"


def test_check_message_queue_empty_task_id():
    assert hook_compat.check_message_queue("") == []
    assert hook_compat.check_message_queue(None) == []


def test_queue_message_appends(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    r1 = hook_compat.queue_message("task-x", "hello")
    r2 = hook_compat.queue_message("task-x", "world")
    assert r1["queued"] is True
    assert r2["queued"] is True
    # Now drain
    msgs = hook_compat.check_message_queue("task-x")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "hello"
    assert msgs[1]["content"] == "world"
    assert msgs[0]["sender"] == "user"  # default


def test_queue_message_requires_content(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    assert hook_compat.queue_message("task-x", "")["queued"] is False
    assert hook_compat.queue_message("", "x")["queued"] is False


def test_queue_then_check_roundtrip(monkeypatch, tmp_path):
    """Full roundtrip: queue 3 messages, check, assert drained."""
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    for i in range(3):
        hook_compat.queue_message("task-rt", f"msg {i}", sender=f"user{i}")
    msgs = hook_compat.check_message_queue("task-rt")
    assert len(msgs) == 3
    assert [m["content"] for m in msgs] == ["msg 0", "msg 1", "msg 2"]
    # Second drain returns empty
    assert hook_compat.check_message_queue("task-rt") == []


# ────────────────────────────────────────────────────────────────────────────
# safety_net_pr
# ────────────────────────────────────────────────────────────────────────────


def _init_git(tmp_path):
    """Initialize a minimal git repo in tmp_path for testing."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=str(tmp_path), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), check=True,
    )
    # Initial commit so HEAD exists
    (tmp_path / "README.md").write_text("initial")
    subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"],
        cwd=str(tmp_path), check=True,
    )


def test_safety_net_pr_clean_work_dir(tmp_path):
    _init_git(tmp_path)
    task = {"id": "task-clean", "title": "test"}
    result = hook_compat.safety_net_pr(
        tmp_path, task, auto_open_pr=False,
    )
    assert result["committed"] is False
    assert "clean" in result["reason"]


def test_safety_net_pr_commits_dirty_work_dir(tmp_path):
    _init_git(tmp_path)
    # Make the work dir dirty
    (tmp_path / "new_file.py").write_text("# auto-created by agent\n")
    (tmp_path / "another.txt").write_text("data\n")

    task = {"id": "task-dirty", "title": "safety net test"}
    result = hook_compat.safety_net_pr(
        tmp_path, task, auto_open_pr=False,
    )
    assert result["committed"] is True
    assert result["commit_sha"] != ""
    assert len(result["commit_sha"]) >= 7  # short sha

    # Verify the commit actually landed
    log = subprocess.run(
        ["git", "log", "-1", "--oneline"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert "safety net test" in log.stdout


def test_safety_net_pr_nonexistent_work_dir():
    task = {"id": "task-x", "title": "t"}
    result = hook_compat.safety_net_pr(
        "/nonexistent/path/xyz", task, auto_open_pr=False,
    )
    assert result["committed"] is False
    assert "does not exist" in result["reason"]


def test_safety_net_pr_skips_pr_when_gh_missing(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "changed.py").write_text("x")
    task = {"id": "task-nogh", "title": "test"}

    # Mock shutil.which to return None for 'gh'
    with patch("shutil.which", return_value=None):
        result = hook_compat.safety_net_pr(tmp_path, task, auto_open_pr=True)

    assert result["committed"] is True
    assert result["pr_created"] is False
    assert "gh CLI not on PATH" in result["reason"]


# ────────────────────────────────────────────────────────────────────────────
# tool_error_middleware
# ────────────────────────────────────────────────────────────────────────────


def test_tool_error_middleware_passthrough_on_success():
    @hook_compat.tool_error_middleware
    def good_tool(x, y=10):
        return {"result": x + y}

    r = good_tool(5, y=3)
    assert r == {"result": 8}


def test_tool_error_middleware_catches_exception():
    @hook_compat.tool_error_middleware
    def bad_tool(**kwargs):
        raise RuntimeError("something broke")

    r = bad_tool()
    assert isinstance(r, dict)
    assert r["error"] == "something broke"
    assert r["error_type"] == "RuntimeError"
    assert r["tool_name"] == "bad_tool"
    assert "traceback" in r
    assert "RuntimeError" in r["traceback"]


def test_tool_error_middleware_preserves_name():
    @hook_compat.tool_error_middleware
    def named_tool():
        return "ok"

    # @functools.wraps preserves __name__
    assert named_tool.__name__ == "named_tool"


def test_tool_error_middleware_catches_various_exceptions():
    @hook_compat.tool_error_middleware
    def value_err_tool():
        raise ValueError("bad value")

    @hook_compat.tool_error_middleware
    def key_err_tool():
        raise KeyError("missing")

    r1 = value_err_tool()
    r2 = key_err_tool()
    assert r1["error_type"] == "ValueError"
    assert r2["error_type"] == "KeyError"
