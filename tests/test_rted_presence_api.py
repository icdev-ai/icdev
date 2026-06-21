# CUI // SP-CTI
"""Integration tests for presence stream + ping API endpoints (rted-pres-02).

Tests:
  POST /api/doc/<doc_id>/presence/ping  → 204, updates DB
  GET  /api/doc/<doc_id>/presence/stream → text/event-stream, 'presence' event

Acceptance criteria:
  GIVEN ping with section_id=S WHEN stream consumed THEN user appears with section_id=S.
  GIVEN no pings for 120s WHEN stream consumed THEN stale user not in event.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.document_intelligence.blueprint as _bp_mod


# ── SQLite shim (replaces %s → ? for presence_registry PG-style SQL) ─────────

class _FakeConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.commit()


@pytest.fixture()
def db(tmp_path):
    """Return path to a fresh SQLite file shared by all connections in this test."""
    return str(tmp_path / "presence_api_test.db")


@pytest.fixture()
def app(db):
    flask_app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
        ),
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    def _make_shim():
        return _FakeConn(sqlite3.connect(db))

    @contextmanager
    def _fake_get_connection():
        c = _make_shim()
        try:
            yield c
        finally:
            c.commit()

    patches = [
        patch("tools.document_intelligence.blueprint._conn", _make_shim),
        patch("tools.document_intelligence.presence_registry.get_connection", _fake_get_connection),
    ]
    for p in patches:
        p.start()

    from tools.document_intelligence.blueprint import dic_bp
    flask_app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    yield flask_app

    for p in patches:
        p.stop()


@pytest.fixture()
def client(app):
    return app.test_client()


_BASE = "/document-intelligence"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sse_data(response_text: str) -> list:
    """Extract the first 'data:' payload from an SSE response and parse JSON."""
    for line in response_text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    return []


# ── Ping endpoint tests ───────────────────────────────────────────────────────

def test_ping_returns_204(client):
    r = client.post(f"{_BASE}/api/doc/doc-p01/presence/ping", json={"section_id": "s1"})
    assert r.status_code == 204
    assert r.data == b""


def test_ping_missing_body_returns_204(client):
    """Empty body is tolerated; section_id defaults to empty string."""
    r = client.post(f"{_BASE}/api/doc/doc-p02/presence/ping",
                    content_type="application/json", data="{}")
    assert r.status_code == 204


def test_ping_creates_presence_record(client):
    """After a ping, get_presence returns the user with correct section."""
    client.post(f"{_BASE}/api/doc/doc-p03/presence/ping", json={"section_id": "intro"})

    from tools.document_intelligence.presence_registry import get_presence
    users = get_presence("doc-p03")
    assert len(users) == 1
    assert users[0]["user_id"] == "current_user"
    assert users[0]["active_section_id"] == "intro"


def test_ping_updates_active_section(client):
    """Second ping updates active_section_id, not creates a duplicate."""
    client.post(f"{_BASE}/api/doc/doc-p04/presence/ping", json={"section_id": "s1"})
    client.post(f"{_BASE}/api/doc/doc-p04/presence/ping", json={"section_id": "s2"})

    from tools.document_intelligence.presence_registry import get_presence
    users = get_presence("doc-p04")
    assert len(users) == 1
    assert users[0]["active_section_id"] == "s2"


# ── Stream endpoint tests ─────────────────────────────────────────────────────

def test_stream_returns_event_stream_mimetype(monkeypatch, client):
    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s01/presence/stream")
    assert r.status_code == 200
    assert "text/event-stream" in r.content_type


def test_stream_emits_event_and_data_lines(monkeypatch, client):
    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s02/presence/stream")
    text = r.data.decode()
    assert "event: presence" in text
    assert "data: " in text


def test_stream_data_is_json_list(monkeypatch, client):
    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s03/presence/stream")
    payload = _parse_sse_data(r.data.decode())
    assert isinstance(payload, list)


def test_stream_contains_pinged_user(monkeypatch, client):
    """GIVEN ping with section_id=S WHEN stream consumed THEN user in event with active_section_id=S."""
    client.post(f"{_BASE}/api/doc/doc-s04/presence/ping", json={"section_id": "conclusion"})

    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s04/presence/stream")
    payload = _parse_sse_data(r.data.decode())

    assert len(payload) == 1
    assert payload[0]["user_id"] == "current_user"
    assert payload[0]["active_section_id"] == "conclusion"


def test_stream_multiple_users(monkeypatch, client):
    """Two pings from same user only produce one presence record (session reuse)."""
    client.post(f"{_BASE}/api/doc/doc-s05/presence/ping", json={"section_id": "a"})
    # Same user (current_user) pings again — should update, not duplicate
    client.post(f"{_BASE}/api/doc/doc-s05/presence/ping", json={"section_id": "b"})

    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s05/presence/stream")
    payload = _parse_sse_data(r.data.decode())
    assert len(payload) == 1
    assert payload[0]["active_section_id"] == "b"


def test_stream_stale_user_not_emitted(monkeypatch, client, db):
    """GIVEN presence row with past expires_at WHEN stream consumed THEN user absent."""
    past = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dic_presence_sessions (
            session_key TEXT PRIMARY KEY, doc_id TEXT NOT NULL, user_id TEXT NOT NULL,
            joined_at TEXT NOT NULL, last_seen TEXT NOT NULL, expires_at TEXT NOT NULL,
            active_section_id TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
        )
    """)
    conn.execute(
        "INSERT INTO dic_presence_sessions "
        "(session_key, doc_id, user_id, joined_at, last_seen, expires_at, active_section_id, classification) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("ps_stale_api", "doc-s06", "stale_user", past, past, past, "old-section", "CUI"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s06/presence/stream")
    payload = _parse_sse_data(r.data.decode())
    user_ids = [u["user_id"] for u in payload]
    assert "stale_user" not in user_ids


def test_stream_no_header_cache_control(monkeypatch, client):
    """SSE response must carry no-cache and no-buffering headers."""
    monkeypatch.setattr(_bp_mod, "_STREAM_MAX_POLLS", 1)
    r = client.get(f"{_BASE}/api/doc/doc-s07/presence/stream")
    assert r.headers.get("Cache-Control") == "no-cache"
    assert r.headers.get("X-Accel-Buffering") == "no"
