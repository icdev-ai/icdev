# CUI // SP-CTI
"""Tests for /api/ace/coworkers/<coworker_id>/stats and /api/ace/<id>/stream."""
from __future__ import annotations

import json
import threading

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Minimal Flask app with ace_api_bp registered (SQLite in-memory)."""
    import os

    os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

    from flask import Flask
    from icdev.tools.ace import blueprint as bp_mod
    from icdev.tools.ace.blueprint import ace_api_bp

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    # Suppress DB init attempts — tests stub _db() per-test.
    bp_mod._state["db_ready"] = True

    # Avoid duplicate registration across test runs
    if "ace_api" not in flask_app.blueprints:
        flask_app.register_blueprint(ace_api_bp)

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Stats route
# ---------------------------------------------------------------------------

class TestCoworkerStatsRoute:
    def test_stats_returns_200(self, client, monkeypatch):
        # Stub _db() to return an in-memory SQLite conn with the needed tables
        import sqlite3
        from icdev.tools.ace import blueprint as bp_mod

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE ace_messages (id TEXT, instance_id TEXT, coworker_id TEXT, "
            "message_type TEXT, role TEXT, content TEXT, metadata_json TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE ace_audit_log (id TEXT, instance_id TEXT, coworker_id TEXT, "
            "action TEXT, detail TEXT, actor TEXT, created_at TEXT)"
        )
        conn.commit()

        monkeypatch.setattr(bp_mod, "_db", lambda: conn)

        # list_sessions may not be available in test env
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.list_sessions",
            lambda **kw: {"sessions": [], "total": 0, "count": 0},
            raising=False,
        )

        resp = client.get("/api/ace/coworkers/cw-test-1/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["coworker_id"] == "cw-test-1"
        assert "message_count" in data
        assert "tool_call_count" in data
        assert "instances" in data

    def test_stats_with_messages(self, client, monkeypatch):
        import sqlite3
        from icdev.tools.ace import blueprint as bp_mod

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE ace_messages (id TEXT, instance_id TEXT, coworker_id TEXT, "
            "message_type TEXT, role TEXT, content TEXT, metadata_json TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE ace_audit_log (id TEXT, instance_id TEXT, coworker_id TEXT, "
            "action TEXT, detail TEXT, actor TEXT, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO ace_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("m1", "inst-x", "cw-abc", "text", "assistant", "hello", "{}", "2026-01-01"),
                ("m2", "inst-x", "cw-abc", "text", "assistant", "world", "{}", "2026-01-02"),
                ("m3", "inst-y", "cw-abc", "text", "assistant", "!", "{}", "2026-01-03"),
            ],
        )
        conn.execute(
            "INSERT INTO ace_audit_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a1", "inst-x", "cw-abc", "agent_turn", "turn=1", "system", "2026-01-01"),
        )
        conn.commit()

        monkeypatch.setattr(bp_mod, "_db", lambda: conn)
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop_session.list_sessions",
            lambda **kw: {"sessions": [], "total": 3, "count": 3},
            raising=False,
        )

        resp = client.get("/api/ace/coworkers/cw-abc/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["message_count"] == 3
        assert data["tool_call_count"] == 1
        assert data["session_count"] == 3
        assert set(data["instances"]) == {"inst-x", "inst-y"}


# ---------------------------------------------------------------------------
# SSE stream route (offline — no real loop running)
# ---------------------------------------------------------------------------

class TestSSEStreamRoute:
    def test_stream_returns_event_stream(self, client, monkeypatch):
        """Subscribe, publish a loop_done event from another thread, verify SSE data."""
        from icdev.tools.ace import event_bus as eb

        def _publish_after_delay():
            import time; time.sleep(0.1)
            eb.publish("inst-sse-1", {
                "type": "loop_done", "coworker_id": "cw-1",
                "result_subtype": "done", "turns": 3, "done": True,
                "session_id": "s-001",
            })

        t = threading.Thread(target=_publish_after_delay, daemon=True)
        t.start()

        resp = client.get("/api/ace/inst-sse-1/stream?timeout=2")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type
        raw = resp.data.decode()
        # Should contain data: {...} line
        data_lines = [l for l in raw.splitlines() if l.startswith("data:")]
        assert data_lines, f"no data: lines in SSE output: {raw!r}"
        payload = json.loads(data_lines[0][len("data:"):].strip())
        assert payload["type"] == "loop_done"
        t.join(timeout=3)

    def test_stream_includes_ping_on_timeout(self, client):
        """With timeout=0.05 and no events, generator yields a ping keep-alive."""
        resp = client.get("/api/ace/inst-ping-test/stream?timeout=0.05&max_pings=1")
        assert resp.status_code == 200
        raw = resp.data.decode()
        assert ": ping" in raw


# ---------------------------------------------------------------------------
# event_bus wired into coworker_thread (unit-level smoke)
# ---------------------------------------------------------------------------

class TestEventBusWiring:
    def test_on_agent_turn_publishes_event(self, monkeypatch):
        """CoWorkerThread._on_agent_turn publishes to the event bus."""
        from icdev.tools.ace import event_bus as eb
        from icdev.tools.ace import coworker_thread as ct_mod

        received = []

        orig_publish = eb.publish
        monkeypatch.setattr(eb, "publish", lambda iid, ev: received.append((iid, ev)))

        class FakeSpec:
            coworker_id = "cw-test-wiring"
            role_id = "analyst"
            trust_tier = "green"
            folder_access = []
            icdev_tools = []
            coordination_namespace = None

        class FakeMB:
            def subscribe(self, *a, **kw): pass
            def publish(self, *a, **kw): pass

        thread = ct_mod.CoWorkerThread(
            spec=FakeSpec(),
            instance_id="inst-wiring",
            message_bus=FakeMB(),
            trust_kernel=None,
        )

        class FakeResponse:
            content = "hello"
            tool_calls = [{"name": "read_file", "input": {"path": "x.py"}}]

        # Call the turn hook directly
        thread._on_agent_turn(0, FakeResponse(), [])

        assert any(
            ev.get("type") == "agent_turn" and iid == "inst-wiring"
            for iid, ev in received
        )
