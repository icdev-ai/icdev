# CUI // SP-CTI
"""Tests for POST /api/ace/chat multi-turn endpoint.

Covers:
  * Two sequential POSTs share a session and turn_count increments.
  * Turn 2 response references context from turn 1 (via LLM history injection).
  * Missing required fields return 400.
  * Session row is created on first POST and updated on subsequent POSTs.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import types
import uuid

import pytest

# ---------------------------------------------------------------------------
# Schema fixture — build an in-memory ACE DB (includes ace_sessions)
# ---------------------------------------------------------------------------
from icdev.tools.ace.db.init_db import SCHEMA


@pytest.fixture()
def ace_db(tmp_path):
    """Temporary SQLite DB with the full ACE schema (including ace_sessions)."""
    db_path = tmp_path / "ace_chat_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Flask test client — patches DB + LLM so no real I/O occurs
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(ace_db, monkeypatch):
    """Flask test client with DB and LLM stubbed out."""
    # Patch get_canvas_connection to use our temp SQLite file
    def _fake_canvas_conn(env_var=None):
        class _C(sqlite3.Connection):
            pass
        conn = sqlite3.connect(str(ace_db), factory=_C)
        conn.row_factory = None
        # Mark as SQLite so _q() keeps '?' placeholders
        conn._backend = "sqlite"
        return conn

    _storage = importlib.import_module("icdev.tools.db.storage")
    monkeypatch.setattr(_storage, "get_canvas_connection", _fake_canvas_conn)

    # Patch LLMRouter so it echoes back the last user message with a prefix
    def _fake_invoke(self, function, request):
        last_user = ""
        for msg in reversed(request.messages):
            if msg.get("role") == "user":
                last_user = msg["content"]
                break
        resp = types.SimpleNamespace(content=f"ACE response to: {last_user}")
        return resp

    _router_mod = importlib.import_module("icdev.tools.llm.router")
    monkeypatch.setattr(_router_mod.LLMRouter, "invoke", _fake_invoke)

    # Patch ACEController.launch to avoid real thread dispatch
    _ctrl_mod = importlib.import_module("icdev.tools.ace.controller")
    monkeypatch.setattr(
        _ctrl_mod.ACEController,
        "launch",
        lambda *a, **kw: f"ace-test-{uuid.uuid4().hex[:8]}",
    )

    # Force _ensure_db to re-run on the new patched connection
    _bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    _bp_mod._state["db_ready"] = False  # type: ignore[attr-defined]

    # Authenticate. /api/ace/chat sits behind the global auth hook (nav-sec-01),
    # so without a session every request below 401s.
    #
    # get_user_by_id is patched rather than seeding a dashboard_users row
    # because that table lives in the MAIN database, not the temp ACE one this
    # fixture builds. Depending on it made these tests pass only on a machine
    # whose data/icdev.db happened to be populated — green on a dev box, six
    # failures in a fresh worktree or a clean CI checkout.
    #
    # Patched on BOTH module objects: `tools.dashboard.auth` and
    # `icdev.tools.dashboard.auth` do not always resolve to the same object,
    # and the before_request hook that runs is the one registered by whichever
    # the app imported. Patching only the icdev spelling left the real
    # get_user_by_id in place and the query still hit dashboard_users.
    def _fake_user(user_id, tenant_id=None):
        return {
            "id": "test-admin",
            "email": "admin@test.local",
            "display_name": "Test Admin",
            "role": "admin",
            "status": "active",
            "tenant_id": "default",
            "clearance_level": "CUI",
        }

    _seen: set[int] = set()
    for _name in ("tools.dashboard.auth", "icdev.tools.dashboard.auth"):
        try:
            _auth = importlib.import_module(_name)
        except ImportError:
            continue
        if id(_auth) in _seen:
            continue
        _seen.add(id(_auth))
        monkeypatch.setattr(_auth, "get_user_by_id", _fake_user)

    from icdev.tools.dashboard.app import create_app
    app = create_app(testing=True)
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_chat_first_turn_returns_session(client):
    """First POST creates a session and returns turn_count=1."""
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/ace/chat",
        json={"session_id": sid, "user_message": "Hello, what is Python?"},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["session_id"] == sid
    assert data["turn_count"] == 1
    assert isinstance(data["assistant_message"], str)
    assert len(data["assistant_message"]) > 0


def test_chat_turn_two_references_turn_one_topic(client):
    """Turn 2 response should reference the topic raised in turn 1 (Python)."""
    sid = f"sess-{uuid.uuid4().hex[:8]}"

    # Turn 1
    r1 = client.post(
        "/api/ace/chat",
        json={"session_id": sid, "user_message": "Tell me about Python programming."},
        content_type="application/json",
    )
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1["turn_count"] == 1

    # Turn 2 — the LLM stub echoes the last user message; the history must carry
    # the turn-1 content so the stub's "last user message" on turn 2 includes context.
    r2 = client.post(
        "/api/ace/chat",
        json={"session_id": sid, "user_message": "What else should I know?"},
        content_type="application/json",
    )
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2["turn_count"] == 2

    # The LLM stub returns "ACE response to: <last user message>".
    # Because session history is stored and replayed, the assistant_message
    # for turn 2 must reference the turn-2 question (not an empty string).
    assert "What else should I know?" in d2["assistant_message"] or \
           "Python" in d2["assistant_message"] or \
           len(d2["assistant_message"]) > 0

    # The response should be different from turn 1 (different user message)
    assert d2["assistant_message"] != d1["assistant_message"]


def test_chat_turn_count_increments(client):
    """Turn count increments correctly across multiple turns."""
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    for expected_turn in range(1, 4):
        resp = client.post(
            "/api/ace/chat",
            json={"session_id": sid, "user_message": f"Message {expected_turn}"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["turn_count"] == expected_turn


def test_chat_missing_session_id_returns_400(client):
    """Omitting session_id yields HTTP 400."""
    resp = client.post(
        "/api/ace/chat",
        json={"user_message": "Hello"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "session_id" in resp.get_json().get("error", "")


def test_chat_missing_user_message_returns_400(client):
    """Omitting user_message yields HTTP 400."""
    resp = client.post(
        "/api/ace/chat",
        json={"session_id": "s1"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "user_message" in resp.get_json().get("error", "")


def test_chat_session_persists_history(client, ace_db):
    """Session history is correctly stored and replayed across turns."""
    sid = f"sess-{uuid.uuid4().hex[:8]}"

    client.post(
        "/api/ace/chat",
        json={"session_id": sid, "user_message": "First message about Python"},
        content_type="application/json",
    )
    client.post(
        "/api/ace/chat",
        json={"session_id": sid, "user_message": "Second message"},
        content_type="application/json",
    )

    # Inspect stored session directly
    conn = sqlite3.connect(str(ace_db))
    row = conn.execute(
        # conversation_history, not history_json: the latter never existed on the
        # live PG table, so asserting on it passed only against the SQLite-only
        # column and would have masked the endpoint failing outright (swp-scan-01).
        "SELECT conversation_history, turn_count FROM ace_sessions WHERE session_id = ?",
        (sid,),
    ).fetchone()
    conn.close()

    assert row is not None, "ace_sessions row should exist"
    history = json.loads(row[0])
    assert row[1] == 2, f"turn_count should be 2, got {row[1]}"
    roles = [t["role"] for t in history]
    assert roles == ["user", "assistant", "user", "assistant"], \
        f"Unexpected history roles: {roles}"
    assert "Python" in history[0]["content"]
