# CUI // SP-CTI
"""Tests for icdev.tools.llm.agent_hitl — mid-turn HITL checkpoint hook.

All tests use an in-memory SQLite DB via a _NoClose wrapper (prevents the
connection from being closed between writes and reads within the same test).
The module-level ``get_canvas_connection`` reference is monkeypatched so no
real ACE canvas DB is required.
"""
from __future__ import annotations

import json
import sqlite3
import threading



# ---------------------------------------------------------------------------
# Test helpers — in-memory DB fixture
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_hitl_pending (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL DEFAULT '',
    instance_id     TEXT NOT NULL DEFAULT '',
    coworker_id     TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL DEFAULT '',
    tool_input_json TEXT NOT NULL DEFAULT '{}',
    detail          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);
CREATE TABLE IF NOT EXISTS ace_audit_log (
    id          TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    instance_id TEXT NOT NULL DEFAULT '',
    coworker_id TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class _NoClose:
    """Delegate all attrs to the real connection but suppress close() so the
    in-memory SQLite DB survives between the _write_pending and _check_resolution
    calls within a single test."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def __getattr__(self, name: str):
        return getattr(self._c, name)

    def close(self) -> None:  # no-op
        pass


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.commit()
    return _NoClose(conn)


# ---------------------------------------------------------------------------
# Tests — build_hitl_hook
# ---------------------------------------------------------------------------


class TestBuildHitlHook:
    def test_empty_triggers_returns_passthrough(self):
        from icdev.tools.llm.agent_hitl import build_hitl_hook
        hook = build_hitl_hook([], "inst", "cw")
        assert hook("write_file", {"path": "x.py", "content": "y"}) is None

    def test_non_trigger_tool_passes_immediately(self, monkeypatch):
        from icdev.tools.llm.agent_hitl import build_hitl_hook
        hook = build_hitl_hook({"write_file"}, "inst", "cw")
        # list_files is not in trigger set — must return None with no DB access
        assert hook("list_files", {"path": "."}) is None

    def test_returns_callable(self):
        from icdev.tools.llm.agent_hitl import build_hitl_hook
        hook = build_hitl_hook({"write_file"}, "inst", "cw")
        assert callable(hook)

    def test_frozenset_triggers(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m
        monkeypatch.setattr(_m, "get_canvas_connection", lambda env: _make_conn())
        from icdev.tools.llm.agent_hitl import build_hitl_hook

        stop = threading.Event()
        stop.set()  # immediate stop → returns error string
        hook = build_hitl_hook(frozenset({"run_tool"}), "inst", "cw", stop_event=stop)
        result = hook("run_tool", {"command": "python tools/x.py"})
        assert result is not None and "cancelled" in result.lower()


# ---------------------------------------------------------------------------
# Tests — _checkpoint internals
# ---------------------------------------------------------------------------


class TestCheckpointApprove:
    def test_approved_returns_none(self, monkeypatch):
        """Simulate approval after first poll by inserting hitl_resolved row."""
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        call_count = [0]

        def _fake_conn(env):
            call_count[0] += 1
            # After the write_pending call, insert a resolved row so the poll sees it.
            if call_count[0] > 1:
                conn._c.execute(
                    "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, actor, created_at) "
                    "VALUES ('inst', 'cw', 'hitl_resolved', "
                    "(SELECT detail FROM agent_hitl_pending LIMIT 1), 'test', datetime('now'))"
                )
                conn._c.commit()
            return conn

        monkeypatch.setattr(_m, "get_canvas_connection", _fake_conn)

        hook = _m.build_hitl_hook({"write_file"}, "inst", "cw", poll_interval_seconds=0.01)
        result = hook("write_file", {"path": "x.py", "content": "hello"})
        assert result is None

    def test_pending_row_written_on_trigger(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        call_count = [0]

        def _fake_conn(env):
            call_count[0] += 1
            if call_count[0] > 1:
                detail = conn._c.execute(
                    "SELECT detail FROM agent_hitl_pending LIMIT 1"
                ).fetchone()
                if detail:
                    conn._c.execute(
                        "INSERT INTO ace_audit_log "
                        "(instance_id, coworker_id, action, detail, actor, created_at) "
                        "VALUES ('inst', 'cw', 'hitl_resolved', ?, 'test', datetime('now'))",
                        (detail[0],),
                    )
                    conn._c.commit()
            return conn

        monkeypatch.setattr(_m, "get_canvas_connection", _fake_conn)
        hook = _m.build_hitl_hook({"run_tool"}, "inst", "cw", poll_interval_seconds=0.01)
        hook("run_tool", {"command": "python tools/x.py"})

        rows = conn._c.execute("SELECT * FROM agent_hitl_pending").fetchall()
        assert len(rows) == 1
        assert rows[0][4] == "run_tool"  # tool_name column

    def test_tool_input_stored_as_json(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        call_count = [0]

        def _fake_conn(env):
            call_count[0] += 1
            if call_count[0] > 1:
                d = conn._c.execute("SELECT detail FROM agent_hitl_pending LIMIT 1").fetchone()
                if d:
                    conn._c.execute(
                        "INSERT INTO ace_audit_log "
                        "(instance_id, coworker_id, action, detail, actor, created_at) "
                        "VALUES ('inst', 'cw', 'hitl_resolved', ?, 'test', datetime('now'))",
                        (d[0],),
                    )
                    conn._c.commit()
            return conn

        monkeypatch.setattr(_m, "get_canvas_connection", _fake_conn)
        hook = _m.build_hitl_hook({"write_file"}, "inst", "cw", poll_interval_seconds=0.01)
        hook("write_file", {"path": "data/x.json", "content": '{"key": "val"}'})

        row = conn._c.execute("SELECT tool_input_json FROM agent_hitl_pending LIMIT 1").fetchone()
        assert row is not None
        parsed = json.loads(row[0])
        assert parsed["path"] == "data/x.json"


class TestCheckpointReject:
    def test_rejected_returns_error_string(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        call_count = [0]

        def _fake_conn(env):
            call_count[0] += 1
            if call_count[0] > 1:
                d = conn._c.execute("SELECT detail FROM agent_hitl_pending LIMIT 1").fetchone()
                if d:
                    conn._c.execute(
                        "INSERT INTO ace_audit_log "
                        "(instance_id, coworker_id, action, detail, actor, created_at) "
                        "VALUES ('inst', 'cw', 'hitl_rejected', ?, 'test', datetime('now'))",
                        (d[0],),
                    )
                    conn._c.commit()
            return conn

        monkeypatch.setattr(_m, "get_canvas_connection", _fake_conn)
        hook = _m.build_hitl_hook({"write_file"}, "inst", "cw", poll_interval_seconds=0.01)
        result = hook("write_file", {"path": "x.py", "content": ""})
        assert result is not None
        assert "rejected" in result.lower()
        assert "write_file" in result

    def test_status_updated_to_rejected(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        call_count = [0]

        def _fake_conn(env):
            call_count[0] += 1
            if call_count[0] > 1:
                d = conn._c.execute("SELECT detail FROM agent_hitl_pending LIMIT 1").fetchone()
                if d:
                    conn._c.execute(
                        "INSERT INTO ace_audit_log "
                        "(instance_id, coworker_id, action, detail, actor, created_at) "
                        "VALUES ('inst', 'cw', 'hitl_rejected', ?, 'test', datetime('now'))",
                        (d[0],),
                    )
                    conn._c.commit()
            return conn

        monkeypatch.setattr(_m, "get_canvas_connection", _fake_conn)
        hook = _m.build_hitl_hook({"write_file"}, "inst", "cw", poll_interval_seconds=0.01)
        hook("write_file", {"path": "x.py", "content": ""})

        row = conn._c.execute("SELECT status FROM agent_hitl_pending LIMIT 1").fetchone()
        assert row[0] == "rejected"


class TestCheckpointTimeout:
    def test_timeout_returns_error_string(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        monkeypatch.setattr(_m, "get_canvas_connection", lambda env: conn)
        hook = _m.build_hitl_hook(
            {"write_file"}, "inst", "cw",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
        )
        result = hook("write_file", {"path": "x.py", "content": ""})
        assert result is not None
        assert "timed out" in result.lower()

    def test_timeout_status_updated(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        monkeypatch.setattr(_m, "get_canvas_connection", lambda env: conn)
        hook = _m.build_hitl_hook(
            {"run_tool"}, "inst", "cw",
            timeout_seconds=1,
            poll_interval_seconds=0.1,
        )
        hook("run_tool", {"command": "python tools/x.py"})

        row = conn._c.execute("SELECT status FROM agent_hitl_pending LIMIT 1").fetchone()
        assert row[0] == "timed_out"


class TestCheckpointStopEvent:
    def test_stop_event_blocks_immediately(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        monkeypatch.setattr(_m, "get_canvas_connection", lambda env: conn)
        stop = threading.Event()
        stop.set()
        hook = _m.build_hitl_hook({"write_file"}, "inst", "cw", stop_event=stop)
        result = hook("write_file", {"path": "x.py", "content": ""})
        assert result is not None
        assert "cancelled" in result.lower()


class TestCheckpointDBUnavailable:
    def test_graceful_degrade_when_db_none(self, monkeypatch):
        """No DB → hook degrades gracefully (returns None = allow)."""
        import icdev.tools.llm.agent_hitl as _m

        monkeypatch.setattr(_m, "get_canvas_connection", None)
        # With no DB, the hook can't write pending or poll — it should
        # short-circuit and allow (return None) rather than blocking forever.
        # This tests that _write_pending and _check_resolution are no-ops.
        result = _m._check_resolution("cw", "hitl_agent:write_file:abc12345")
        assert result is None


class TestGetPendingHitl:
    def test_returns_pending_rows(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        conn._c.execute(
            "INSERT INTO agent_hitl_pending "
            "(id, instance_id, coworker_id, tool_name, detail, status, created_at) "
            "VALUES ('id1', 'inst1', 'cw1', 'write_file', 'hitl_agent:wf:abc', 'pending', datetime('now'))"
        )
        conn._c.execute(
            "INSERT INTO agent_hitl_pending "
            "(id, instance_id, coworker_id, tool_name, detail, status, created_at) "
            "VALUES ('id2', 'inst1', 'cw2', 'run_tool', 'hitl_agent:rt:def', 'approved', datetime('now'))"
        )
        conn._c.commit()
        monkeypatch.setattr(_m, "get_canvas_connection", lambda env: conn)

        rows = _m.get_pending_hitl("inst1")
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "write_file"

    def test_different_instance_not_returned(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        conn = _make_conn()
        conn._c.execute(
            "INSERT INTO agent_hitl_pending "
            "(id, instance_id, coworker_id, tool_name, detail, status, created_at) "
            "VALUES ('id3', 'inst2', 'cw1', 'write_file', 'hitl_agent:wf:xyz', 'pending', datetime('now'))"
        )
        conn._c.commit()
        monkeypatch.setattr(_m, "get_canvas_connection", lambda env: conn)

        rows = _m.get_pending_hitl("inst1")
        assert rows == []

    def test_no_db_returns_empty(self, monkeypatch):
        import icdev.tools.llm.agent_hitl as _m

        monkeypatch.setattr(_m, "get_canvas_connection", None)
        assert _m.get_pending_hitl("inst1") == []
