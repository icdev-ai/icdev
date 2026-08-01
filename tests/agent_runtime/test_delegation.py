# CUI // SP-CTI
"""Tests for subagent delegation (sag-del-01).

Hermetic: no real subprocess is spawned and no LLM is called. The parent-side
``delegate_task`` is exercised with a faked ``subprocess.run``; the child-side
``_run_child`` with a faked ``AgentRuntime``. Depth / re-delegation policy is
unit-tested via the environment.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

import tools.agent_runtime.delegation as deleg


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    final_content: str = "  child answer  "
    session_id: str = "sess-child"
    total_input_tokens: int = 3
    total_output_tokens: int = 2
    total_cost_usd: float = 0.0
    messages: list = field(default_factory=list)


class _FakeSession:
    context_id = "ctx-child-1"

    def usage(self) -> dict[str, Any]:
        return {"turns": 1, "context_id": self.context_id}


class _FakeRuntime:
    last_kwargs: dict[str, Any] = {}
    used_toolsets: list[str] | None = None
    raise_on_toolset = False

    def __init__(self, **kwargs: Any) -> None:
        _FakeRuntime.last_kwargs = kwargs
        self.session = _FakeSession()

    def use_toolset(self, bundles, **_kw):
        if _FakeRuntime.raise_on_toolset:
            raise ValueError("bad bundle")
        _FakeRuntime.used_toolsets = list(bundles)
        return bundles

    def run_turn(self, prompt: str):
        self._prompt = prompt
        return _FakeResult()


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Depth / re-delegation policy
# ---------------------------------------------------------------------------


def test_depth_zero_can_delegate(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_DEPTH", raising=False)
    allowed, _ = deleg._can_delegate_here()
    assert allowed is True


def test_leaf_child_cannot_redelegate(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_DEPTH", "1")
    monkeypatch.setenv("ICDEV_SAG_CAN_DELEGATE", "0")
    allowed, reason = deleg._can_delegate_here()
    assert allowed is False
    assert "leaf" in reason


def test_orchestrator_child_may_redelegate(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_DEPTH", "1")
    monkeypatch.setenv("ICDEV_SAG_CAN_DELEGATE", "1")
    allowed, _ = deleg._can_delegate_here()
    assert allowed is True


def test_depth_limit_reached(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_DEPTH", str(deleg.MAX_ORCHESTRATOR_DEPTH))
    monkeypatch.setenv("ICDEV_SAG_CAN_DELEGATE", "1")
    allowed, reason = deleg._can_delegate_here()
    assert allowed is False
    assert "depth limit" in reason


# ---------------------------------------------------------------------------
# delegate_task — parent side (subprocess faked)
# ---------------------------------------------------------------------------


def test_delegate_task_refused_without_spawning(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_DEPTH", "1")
    monkeypatch.setenv("ICDEV_SAG_CAN_DELEGATE", "0")

    def _boom(*a, **k):  # must NOT be called
        raise AssertionError("subprocess.run should not run when refused")

    monkeypatch.setattr(deleg.subprocess, "run", _boom)
    res = deleg.delegate_task("do a thing")
    assert res["status"] == "refused"
    assert res["error"]


def test_delegate_task_ok(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_DEPTH", raising=False)
    captured = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        captured["input"] = kw.get("input")
        payload = deleg._result(
            "g", "leaf", "ok", summary="hi", content="hi", context_id="ctx-9",
            usage={"turns": 1},
        )
        return _completed(stdout="noise\n" + deleg._RESULT_SENTINEL + json.dumps(payload) + "\n")

    monkeypatch.setattr(deleg.subprocess, "run", _fake_run)
    res = deleg.delegate_task("g", toolsets=["file"], role="orchestrator")
    assert res["status"] == "ok"
    assert res["summary"] == "hi"
    assert res["duration_ms"] >= 0
    # child env carries depth 1 and (orchestrator, depth<2) → may delegate
    assert captured["env"]["ICDEV_SAG_DEPTH"] == "1"
    assert captured["env"]["ICDEV_SAG_CAN_DELEGATE"] == "1"
    # job carried the toolset selection
    job = json.loads(captured["input"])
    assert job["toolsets"] == ["file"]


def test_delegate_task_leaf_sets_no_redelegate(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_DEPTH", raising=False)
    captured = {}

    def _fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        payload = deleg._result("g", "leaf", "ok", summary="x")
        return _completed(stdout=deleg._RESULT_SENTINEL + json.dumps(payload))

    monkeypatch.setattr(deleg.subprocess, "run", _fake_run)
    deleg.delegate_task("g", role="leaf")
    assert captured["env"]["ICDEV_SAG_CAN_DELEGATE"] == "0"


def test_delegate_task_timeout(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_DEPTH", raising=False)

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(deleg.subprocess, "run", _raise)
    res = deleg.delegate_task("g", timeout=1)
    assert res["status"] == "timeout"
    assert "exceeded" in res["error"]


def test_delegate_task_nonzero_exit(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_DEPTH", raising=False)
    monkeypatch.setattr(
        deleg.subprocess, "run", lambda *a, **k: _completed(returncode=3, stderr="boom")
    )
    res = deleg.delegate_task("g")
    assert res["status"] == "error"
    assert "exited 3" in res["error"]


def test_delegate_task_unparseable(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_DEPTH", raising=False)
    monkeypatch.setattr(
        deleg.subprocess, "run", lambda *a, **k: _completed(stdout="not json at all")
    )
    res = deleg.delegate_task("g")
    assert res["status"] == "error"


def test_extract_result_line_picks_last():
    s = (
        deleg._RESULT_SENTINEL + '{"a":1}\n'
        "middle\n"
        + deleg._RESULT_SENTINEL + '{"a":2}\n'
    )
    assert json.loads(deleg._extract_result_line(s)) == {"a": 2}


# ---------------------------------------------------------------------------
# delegate_batch
# ---------------------------------------------------------------------------


def test_delegate_batch_order_preserved(monkeypatch):
    monkeypatch.setattr(
        deleg, "delegate_task", lambda goal, **k: {"status": "ok", "goal": goal}
    )
    out = deleg.delegate_batch(
        [{"goal": "a"}, {"goal": "b"}, {"goal": "c"}], max_concurrency=2
    )
    assert [r["goal"] for r in out] == ["a", "b", "c"]


def test_delegate_batch_missing_goal(monkeypatch):
    monkeypatch.setattr(deleg, "delegate_task", lambda *a, **k: {"status": "ok"})
    out = deleg.delegate_batch([{"context": "no goal"}])
    assert out[0]["status"] == "error"


def test_delegate_batch_empty():
    assert deleg.delegate_batch([]) == []


# ---------------------------------------------------------------------------
# _run_child — child side (AgentRuntime faked)
# ---------------------------------------------------------------------------


def _patch_child(monkeypatch):
    monkeypatch.setattr("tools.agent_runtime.runtime.AgentRuntime", _FakeRuntime)
    monkeypatch.setattr("tools.agent_runtime.sessions.ensure_chat_tables", lambda: True)
    _FakeRuntime.used_toolsets = None
    _FakeRuntime.raise_on_toolset = False


def test_run_child_ok(monkeypatch):
    _patch_child(monkeypatch)
    out = deleg._run_child(
        {"goal": "summarize", "context": "some ctx", "toolsets": ["file"], "max_cost_usd": 0.5}
    )
    assert out["status"] == "ok"
    assert out["summary"] == "child answer"  # stripped
    assert out["context_id"] == "ctx-child-1"
    assert _FakeRuntime.used_toolsets == ["file"]
    assert _FakeRuntime.last_kwargs.get("max_cost_usd") == 0.5


def test_run_child_empty_goal(monkeypatch):
    _patch_child(monkeypatch)
    out = deleg._run_child({"goal": ""})
    assert out["status"] == "error"


def test_run_child_toolset_failure(monkeypatch):
    _patch_child(monkeypatch)
    _FakeRuntime.raise_on_toolset = True
    out = deleg._run_child({"goal": "g", "toolsets": ["nope"]})
    assert out["status"] == "error"
    assert "toolset" in out["error"]


# ---------------------------------------------------------------------------
# build_delegate_tool
# ---------------------------------------------------------------------------


def test_build_delegate_tool_schema_and_handler(monkeypatch):
    schema, handler = deleg.build_delegate_tool(role="leaf", toolsets=["file"])
    assert schema["function"]["name"] == "delegate_task"
    assert schema["is_read_only"] is False

    monkeypatch.setattr(
        deleg, "delegate_task",
        lambda goal, **k: {"status": "ok", "summary": f"done:{goal}"},
    )
    assert handler({"goal": "task-x"}) == "done:task-x"
    assert handler({}) == "error: 'goal' is required"


def test_build_delegate_tool_handler_reports_failure(monkeypatch):
    _schema, handler = deleg.build_delegate_tool()
    monkeypatch.setattr(
        deleg, "delegate_task",
        lambda goal, **k: {"status": "timeout", "error": "too slow"},
    )
    assert "timeout" in handler({"goal": "x"})


# ---------------------------------------------------------------------------
# child entrypoint
# ---------------------------------------------------------------------------


def test_main_requires_child_flag(capsys):
    assert deleg.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_main_child_bad_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: "{bad")})())
    rc = deleg.main(["--child"])
    assert rc == 1
    out = capsys.readouterr().out
    assert deleg._RESULT_SENTINEL in out
    line = deleg._extract_result_line(out)
    assert json.loads(line)["status"] == "error"
