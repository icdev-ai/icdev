# CUI // SP-CTI
"""Tests for the SAG CLI surface (sag-rt-03).

Covers the headless-construction blocker fix (``AgentRuntime()`` / ``RuntimeSession``
must build a ``ChatManager`` with a default ``user_id``), single-shot end-to-end
turn execution with a mocked LLM, ``--resume`` rehydration, and the
``icdev sessions list|export`` commands. All persistence is faked so the tests
are DB-independent and match the shared conftest schema (no chat tables).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

import tools.agent_runtime.cli as cli_mod
import tools.agent_runtime.sessions as sess_mod
from tools.agent_runtime.runtime import AgentRuntime
from tools.agent_runtime.sessions import RuntimeSession


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeChatManager:
    """In-memory ChatManager stand-in with a shared store (no DB).

    Records ``last_user_id`` at construction so tests can assert the headless
    default is threaded through. The store is class-level so a ``RuntimeSession``
    and a separately-constructed CLI manager observe the same conversations.
    """

    last_user_id: Any = None
    _store: dict[str, dict[str, Any]] = {}
    _counter = 0

    def __init__(self, user_id, tenant_id: str = "") -> None:
        FakeChatManager.last_user_id = user_id
        self.user_id = user_id
        self.tenant_id = tenant_id

    @classmethod
    def reset(cls) -> None:
        cls.last_user_id = None
        cls._store = {}
        cls._counter = 0

    def create_context(self, *, title="", system_prompt=None, classification=None, config=None) -> str:
        FakeChatManager._counter += 1
        cid = f"ctx-{FakeChatManager._counter}"
        self._store[cid] = {
            "context": {
                "id": cid,
                "title": title,
                "status": "active",
                "message_count": 0,
                "context_config": dict(config or {}),
                "user_id": self.user_id,
                "tenant_id": self.tenant_id,
                "last_activity_at": "2026-07-25T00:00:00",
                "updated_at": "2026-07-25T00:00:00",
            },
            "messages": [],
        }
        return cid

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        rec = self._store.setdefault(
            context_id, {"context": {"id": context_id, "context_config": {}}, "messages": []}
        )
        rec["messages"].append(
            {
                "turn_number": len(rec["messages"]) + 1,
                "role": role,
                "content": content,
                "content_type": "text",
                "created_at": "2026-07-25T00:00:00",
            }
        )
        rec["context"]["message_count"] = len(rec["messages"])
        return len(rec["messages"])

    def get_messages(self, context_id, *, limit=200, offset=0):
        return list(self._store.get(context_id, {}).get("messages", []))[offset : offset + limit]

    def get_context(self, context_id):
        rec = self._store.get(context_id)
        return dict(rec["context"]) if rec else None

    def list_contexts(self, *, status="active", limit=50):
        out = []
        for rec in self._store.values():
            c = rec["context"]
            if c.get("user_id") != self.user_id:
                continue
            if status and c.get("status") != status:
                continue
            out.append(dict(c))
        return out[:limit]

    def update_title(self, context_id, title) -> None:
        self._store[context_id]["context"]["title"] = title

    def update_config(self, context_id, updates) -> None:
        self._store[context_id]["context"]["context_config"].update(updates)


@dataclass
class _FakeResult:
    final_content: str = "the answer is 42"
    session_id: str = "sess-abc"
    total_input_tokens: int = 11
    total_output_tokens: int = 7
    total_cost_usd: float = 0.002
    messages: list = field(default_factory=list)


@pytest.fixture
def fake_chat(monkeypatch):
    FakeChatManager.reset()
    # RuntimeSession imports ChatManager at module top.
    monkeypatch.setattr(sess_mod, "ChatManager", FakeChatManager)
    # cli._chat_manager imports from tools.chat.chat_manager at call time.
    monkeypatch.setattr("tools.chat.chat_manager.ChatManager", FakeChatManager)
    # Skip real table provisioning.
    monkeypatch.setattr(sess_mod, "ensure_chat_tables", lambda: True)
    yield FakeChatManager


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch the agent loop and session save so a turn runs without an LLM/DB."""
    def _fake_loop(*_a, **_kw):
        return _FakeResult()

    monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", _fake_loop)
    monkeypatch.setattr(
        "icdev.tools.llm.agent_loop_session.save_session", lambda *a, **k: True
    )
    return _fake_loop


# ---------------------------------------------------------------------------
# Blocker fix: headless construction
# ---------------------------------------------------------------------------


def test_runtime_session_constructs_with_default_user(fake_chat):
    sess = RuntimeSession.create(title="t")
    assert sess.context_id.startswith("ctx-")
    # ChatManager was built with the default headless identity, not no-args.
    assert fake_chat.last_user_id == "default"
    assert sess.user_id == "default"


def test_default_user_id_from_env(fake_chat, monkeypatch):
    monkeypatch.setenv("ICDEV_USER_ID", "alice")
    sess = RuntimeSession.create()
    assert fake_chat.last_user_id == "alice"
    assert sess.user_id == "alice"


def test_agent_runtime_builds_headlessly(fake_chat):
    # This is the exact call that used to raise (ChatManager() missing user_id).
    runtime = AgentRuntime()
    assert runtime.session.context_id.startswith("ctx-")
    assert runtime.user_id == "default"


def test_lazy_manager_rebuilds_with_identity(fake_chat):
    sess = RuntimeSession(context_id="ctx-x", user_id="bob", tenant_id="acme")
    mgr = sess.manager  # lazily constructed
    assert mgr.user_id == "bob"
    assert mgr.tenant_id == "acme"


# ---------------------------------------------------------------------------
# Single-shot end-to-end (mocked LLM)
# ---------------------------------------------------------------------------


def test_single_shot_turn_end_to_end(fake_chat, mock_llm):
    runtime = AgentRuntime(user_id="u1")
    result = runtime.run_turn("what is 6 times 7?")
    assert getattr(result, "final_content", "") == "the answer is 42"
    # transcript recorded (user + assistant)
    msgs = runtime.session.messages()
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    # usage rolled forward
    assert runtime.session.turn_count == 1
    assert runtime.session.resume_session_id == "sess-abc"


def test_chat_main_single_shot_json(fake_chat, mock_llm, capsys):
    rc = cli_mod.chat_main(["-q", "hello", "--json", "--user", "u2"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["response"] == "the answer is 42"
    assert payload["context_id"].startswith("ctx-")
    assert payload["usage"]["turns"] == 1


def test_chat_main_single_shot_plain(fake_chat, mock_llm, capsys):
    rc = cli_mod.chat_main(["-q", "hello"])
    assert rc == 0
    assert "the answer is 42" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_restores_transcript_and_resume_id(fake_chat, mock_llm):
    runtime = AgentRuntime(user_id="u3")
    runtime.run_turn("first question")
    ctx_id = runtime.session.context_id
    # persist() stashed the agent-loop session id on the context.
    assert fake_chat._store[ctx_id]["context"]["context_config"]["resume_session_id"] == "sess-abc"

    # A fresh runtime resumes it.
    runtime2 = AgentRuntime(user_id="u3")
    sess = runtime2.resume_session(ctx_id)
    assert sess.context_id == ctx_id
    assert sess.resume_session_id == "sess-abc"
    assert sess.title  # title carried over


def test_resume_unknown_context_errors(fake_chat, capsys):
    rc = cli_mod.chat_main(["--resume", "ctx-missing", "-q", "hi"])
    assert rc == 2
    assert "cannot resume" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# sessions list / export
# ---------------------------------------------------------------------------


def test_sessions_list(fake_chat, mock_llm, capsys):
    runtime = AgentRuntime(user_id="lister")
    runtime.run_turn("q")
    rc = cli_mod.sessions_main(["list", "--user", "lister", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["sessions"][0]["user_id"] == "lister"


def test_sessions_export_jsonl(fake_chat, mock_llm, capsys):
    runtime = AgentRuntime(user_id="exp")
    runtime.run_turn("q1")
    ctx_id = runtime.session.context_id
    rc = cli_mod.sessions_main(["export", ctx_id, "--user", "exp"])
    assert rc == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    kinds = [json.loads(l)["_type"] for l in lines]
    assert kinds[0] == "context"
    assert "message" in kinds
    # every line is valid JSON (JSONL)
    for l in lines:
        json.loads(l)


def test_sessions_export_unknown(fake_chat, capsys):
    rc = cli_mod.sessions_main(["export", "ctx-nope"])
    assert rc == 2
    assert "no such session" in capsys.readouterr().err
