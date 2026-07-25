# CUI // SP-CTI
"""Unit tests for the standalone agent runtime (SAG sag-rt-01).

Covers the built-in starter toolset (read_file / search_files / health_check
with repo-root confinement) and the AgentRuntime orchestration (turn execution,
usage roll-forward, session resume, and the minimal built-in slash dispatcher /
REPL). Persistence layers (ChatManager, agent_loop_session) are faked so the
tests are DB-independent and match the shared conftest schema, which does not
provision chat tables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import tools.agent_runtime.builtin_tools as bt
import tools.agent_runtime.runtime as rt_mod
import tools.agent_runtime.sessions as sess_mod
from tools.agent_runtime.runtime import AgentRuntime


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeChatManager:
    """In-memory stand-in for ChatManager — no DB."""

    def __init__(self) -> None:
        self._n = 0
        self.contexts: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def create_context(self, *, title="", **_kw) -> str:
        self._n += 1
        cid = f"ctx-{self._n}"
        self.contexts[cid] = {"title": title}
        self.messages[cid] = []
        return cid

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        self.messages.setdefault(context_id, []).append(
            {"role": role, "content": content}
        )
        return len(self.messages[context_id])

    def get_messages(self, context_id, *, limit=200, offset=0):
        return list(self.messages.get(context_id, []))[offset : offset + limit]

    def update_title(self, context_id, title) -> None:
        self.contexts.setdefault(context_id, {})["title"] = title


@dataclass
class _FakeResult:
    """Stand-in for AgentLoopResult."""

    final_content: str = ""
    session_id: str = "sess-1"
    total_input_tokens: int = 10
    total_output_tokens: int = 5
    total_cost_usd: float = 0.001
    messages: list = field(default_factory=list)


@pytest.fixture
def fake_manager(monkeypatch):
    mgr = _FakeChatManager()
    monkeypatch.setattr(sess_mod, "ChatManager", lambda *a, **k: mgr)
    return mgr


@pytest.fixture
def no_save(monkeypatch):
    """Neutralize agent_loop_session.save_session (best-effort persistence)."""
    import icdev.tools.llm.agent_loop_session as als

    calls = []
    monkeypatch.setattr(als, "save_session", lambda *a, **k: calls.append((a, k)) or True)
    return calls


# ---------------------------------------------------------------------------
# Built-in toolset
# ---------------------------------------------------------------------------


def test_toolset_shape():
    tools, handlers = bt.build_builtin_toolset()
    names = {t["function"]["name"] for t in tools}
    assert names == {"read_file", "search_files", "health_check"}
    assert set(handlers) == names
    # read/search are marked read-only for parallel dispatch.
    for t in tools:
        if t["function"]["name"] in ("read_file", "search_files", "health_check"):
            assert t.get("is_read_only") is True


def test_read_file_ok(tmp_path, monkeypatch):
    (tmp_path / "hello.txt").write_text("hi there", encoding="utf-8")
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    out = bt._handle_read_file({"path": "hello.txt"}, None)
    assert out == "hi there"


def test_read_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    assert bt._handle_read_file({"path": "nope.txt"}, None).startswith("error: file not found")


def test_read_file_escape_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    out = bt._handle_read_file({"path": "../../etc/passwd"}, None)
    assert "escapes repository root" in out


def test_read_file_requires_path(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    assert bt._handle_read_file({}, None) == "error: 'path' is required"


def test_search_files_matches(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    out = bt._handle_search_files({"pattern": r"def \w+", "glob": "*.py"}, None)
    assert "a.py:1:" in out
    assert "b.py" not in out


def test_search_files_no_match(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    assert bt._handle_search_files({"pattern": "zzz"}, None) == "(no matches)"


def test_search_files_bad_regex(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "_REPO_ROOT", tmp_path.resolve())
    assert bt._handle_search_files({"pattern": "("}, None).startswith("error: invalid regex")


# ---------------------------------------------------------------------------
# Runtime orchestration
# ---------------------------------------------------------------------------


def test_run_turn_persists_and_rolls_usage(fake_manager, no_save, monkeypatch):
    captured = {}

    def fake_loop(router, **kw):
        captured.update(kw)
        return _FakeResult(final_content="answer-1", session_id="sess-A")

    monkeypatch.setattr(rt_mod, "AgentRuntime", AgentRuntime)  # ensure module import
    monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)

    runtime = AgentRuntime(router=object())
    result = runtime.run_turn("what is foo?")
    assert result.final_content == "answer-1"
    # First turn: no resume id passed.
    assert captured["resume_session_id"] is None
    # Usage rolled forward and resume id captured.
    assert runtime.session.resume_session_id == "sess-A"
    assert runtime.session.turn_count == 1
    u = runtime.session.usage()
    assert u["total_tokens"] == 15
    # Transcript recorded user + assistant.
    msgs = fake_manager.get_messages(runtime.session.context_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_run_turn_resumes_prior_session(fake_manager, no_save, monkeypatch):
    seen_resume = []

    def fake_loop(router, **kw):
        seen_resume.append(kw["resume_session_id"])
        return _FakeResult(final_content="ok", session_id="sess-X")

    monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
    runtime = AgentRuntime(router=object())
    runtime.run_turn("first")
    runtime.run_turn("second")
    assert seen_resume == [None, "sess-X"]


def test_dispatch_builtin_commands(fake_manager, monkeypatch):
    runtime = AgentRuntime(router=object())
    handled, resp, exit_ = runtime.dispatch_command("/tools")
    assert handled and not exit_
    assert "read_file" in resp

    handled, resp, exit_ = runtime.dispatch_command("/exit")
    assert exit_ is True

    old_ctx = runtime.session.context_id
    handled, resp, exit_ = runtime.dispatch_command("/new titled")
    assert runtime.session.context_id != old_ctx
    assert runtime.session.title == "titled"

    handled, resp, exit_ = runtime.dispatch_command("/bogus")
    assert "Unknown command" in resp


def test_dispatch_delegates_to_injected_handler(fake_manager):
    def handler(rt, raw):
        return True, f"custom:{raw}", False

    runtime = AgentRuntime(router=object(), command_handler=handler)
    handled, resp, exit_ = runtime.dispatch_command("/anything")
    assert resp == "custom:/anything"


def test_loop_runs_turn_then_exits(fake_manager, no_save, monkeypatch):
    def fake_loop(router, **kw):
        return _FakeResult(final_content="loop-answer", session_id="s")

    monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
    inputs = iter(["hello agent", "/exit"])
    outputs: list[str] = []
    runtime = AgentRuntime(router=object())
    runtime.loop(input_fn=lambda _p: next(inputs), output_fn=outputs.append, banner=False)
    assert "loop-answer" in outputs
    assert any("Goodbye" in o for o in outputs)


def test_loop_survives_turn_error(fake_manager, monkeypatch):
    def boom(router, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", boom)
    inputs = iter(["trigger error", "/exit"])
    outputs: list[str] = []
    runtime = AgentRuntime(router=object())
    runtime.loop(input_fn=lambda _p: next(inputs), output_fn=outputs.append, banner=False)
    assert any("error: provider down" in o for o in outputs)
