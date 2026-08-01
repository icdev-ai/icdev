# CUI // SP-CTI
"""Tests for session search + streaming wiring (sag-rt-04).

Covers per-turn indexing into the shared FTS store, ``search_sessions`` ctx-id
enrichment, the ``/search`` slash command, ``icdev sessions search`` CLI, and the
``stream_turn`` streaming path (via a faked ``invoke_streaming``). Hermetic: chat
persistence and the session indexer are faked; no DB or LLM.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import tools.agent_runtime.cli as cli_mod
import tools.agent_runtime.commands as cmd_mod
import tools.agent_runtime.sessions as sess_mod
import tools.memory.session_indexer as si_mod
from tools.agent_runtime.runtime import AgentRuntime
from tools.agent_runtime.sessions import RuntimeSession, search_sessions


class _FakeChatManager:
    def __init__(self, *_a, **_k) -> None:
        self._n = 0
        self.messages: dict[str, list] = {}

    def create_context(self, *, title="", **_kw) -> str:
        self._n += 1
        cid = f"ctx-{self._n}"
        self.messages[cid] = []
        return cid

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        self.messages.setdefault(context_id, []).append({"role": role, "content": content})
        return len(self.messages[context_id])

    def get_messages(self, context_id, *, limit=200, offset=0):
        return list(self.messages.get(context_id, []))

    def update_title(self, context_id, title) -> None:
        pass

    def update_config(self, context_id, updates) -> None:
        pass


class _FakeRouter:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.calls: list = []

    def invoke_streaming(self, function, request):
        self.calls.append((function, request))
        for c in self._chunks:
            yield c


@pytest.fixture
def fake_persist(monkeypatch):
    monkeypatch.setattr(sess_mod, "ChatManager", _FakeChatManager)
    monkeypatch.setattr(sess_mod, "ensure_chat_tables", lambda: True)
    # Neutralize the real indexer unless a test overrides it.
    monkeypatch.setattr(si_mod, "index_session_turn", lambda *a, **k: "id")
    yield


# ---------------------------------------------------------------------------
# Per-turn indexing
# ---------------------------------------------------------------------------


def test_record_turn_indexes_with_ctx_tag(fake_persist, monkeypatch):
    captured = []
    monkeypatch.setattr(
        si_mod, "index_session_turn",
        lambda sid, role, content, **kw: captured.append((sid, role, content, kw)) or "eid",
    )
    sess = RuntimeSession.create(title="t")
    sess.record_user("find the bug in parser")
    sess.record_assistant("it is on line 42")

    assert len(captured) == 2
    sid, role, content, kw = captured[0]
    assert role == "user"
    assert f"ctx:{sess.context_id}" in kw["tags"]
    assert "sag" in kw["tags"]


def test_empty_turn_not_indexed(fake_persist, monkeypatch):
    captured = []
    monkeypatch.setattr(
        si_mod, "index_session_turn",
        lambda *a, **k: captured.append(a) or "eid",
    )
    sess = RuntimeSession.create()
    sess.record_assistant("")
    assert captured == []


# ---------------------------------------------------------------------------
# search_sessions
# ---------------------------------------------------------------------------


def test_search_sessions_extracts_ctx(monkeypatch):
    monkeypatch.setattr(
        si_mod, "search_history",
        lambda q, limit=20: [
            {"id": "e1", "content": "the parser bug", "type": "session_user", "tags": "sag,ctx:ctx-77", "score": 1.0},
            {"id": "e2", "content": "no ctx here", "type": "session_user", "tags": "sag", "score": 0.5},
        ],
    )
    out = search_sessions("parser", limit=5)
    assert out[0]["context_id"] == "ctx-77"
    assert out[1]["context_id"] == ""


def test_search_sessions_error_returns_empty(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(si_mod, "search_history", _boom)
    assert search_sessions("x") == []


# ---------------------------------------------------------------------------
# /search slash command
# ---------------------------------------------------------------------------


def test_cmd_search_lists_hits(monkeypatch):
    monkeypatch.setattr(
        sess_mod,
        "search_sessions",
        lambda q, limit=10: [
            {"content": "the parser bug", "type": "session_user", "context_id": "ctx-9"}
        ],
    )
    text, should_exit = cmd_mod._cmd_search(None, "parser")
    assert should_exit is False
    assert "ctx-9" in text
    assert "resume" in text.lower()


def test_cmd_search_requires_query():
    text, _ = cmd_mod._cmd_search(None, "  ")
    assert "Usage" in text


def test_search_registered():
    assert "/search" in cmd_mod.REGISTRY


# ---------------------------------------------------------------------------
# icdev sessions search CLI
# ---------------------------------------------------------------------------


def test_cli_sessions_search_json(monkeypatch, capsys):
    monkeypatch.setattr(
        sess_mod,
        "search_sessions",
        lambda q, limit=20: [{"content": "hit", "type": "session_user", "context_id": "ctx-5"}],
    )
    rc = cli_mod.sessions_main(["search", "parser", "bug", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["results"][0]["context_id"] == "ctx-5"


def test_cli_sessions_search_plain(monkeypatch, capsys):
    monkeypatch.setattr(
        sess_mod,
        "search_sessions",
        lambda q, limit=20: [{"content": "hit text", "type": "session_user", "context_id": "ctx-5"}],
    )
    rc = cli_mod.sessions_main(["search", "parser"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--resume ctx-5" in out


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


def test_stream_turn_accumulates_and_records(fake_persist):
    chunks = [
        {"type": "text", "text": "Hel"},
        {"type": "text", "text": "lo"},
        {"type": "message_stop", "usage": {"input_tokens": 4, "output_tokens": 2}},
    ]
    runtime = AgentRuntime(router=_FakeRouter(chunks))
    deltas: list[str] = []
    out = runtime.stream_turn("hi", on_delta=deltas.append)
    assert out == "Hello"
    assert deltas == ["Hel", "lo"]
    assert runtime.session.turn_count == 1
    assert runtime.session.total_input_tokens == 4
    assert runtime.session.total_output_tokens == 2
    # assistant turn recorded
    msgs = runtime.session.messages()
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "Hello"


def test_stream_turn_error_returns_partial(fake_persist):
    chunks = [
        {"type": "text", "text": "partial"},
        {"type": "error", "error": "boom"},
    ]
    runtime = AgentRuntime(router=_FakeRouter(chunks))
    out = runtime.stream_turn("hi")
    assert out == "partial"


def test_stream_turn_exception_kept_alive(fake_persist):
    class _BadRouter:
        def invoke_streaming(self, *a, **k):
            raise RuntimeError("provider down")

    runtime = AgentRuntime(router=_BadRouter())
    out = runtime.stream_turn("hi")
    assert out.startswith("error:")


def test_loop_streaming_mode(fake_persist, capsys):
    chunks = [
        {"type": "text", "text": "stream "},
        {"type": "text", "text": "reply"},
        {"type": "message_stop", "usage": {}},
    ]
    runtime = AgentRuntime(router=_FakeRouter(chunks))
    inputs = iter(["hello", "/exit"])

    def _in(_prompt):
        return next(inputs)

    outputs: list[str] = []
    runtime.loop(input_fn=_in, output_fn=outputs.append, banner=False, stream=True)
    # streamed text went to stdout
    assert "stream reply" in capsys.readouterr().out
