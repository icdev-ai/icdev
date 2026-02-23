#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/observability/sqlite_tracer.py — SQLite span backend (D280).

Covers:
  - SQLiteSpan creation, attributes, events, status, lifecycle
  - SQLiteTracer span nesting, trace_id inheritance, active span tracking
  - Buffer flush to SQLite DB, thread-safety
  - Query spans with filters
  - Context manager usage
  - Edge cases: double-end, empty flush, missing DB
"""

import json
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_db(path: Path):
    """Create an in-memory-like SQLite DB with otel_spans table at the given path."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db_path(tmp_path):
    """Provide a temp SQLite DB with otel_spans table."""
    return _create_db(tmp_path / "test.db")


@pytest.fixture
def tracer(db_path):
    from tools.observability.sqlite_tracer import SQLiteTracer
    t = SQLiteTracer(db_path=db_path, agent_id="test-agent", project_id="proj-test")
    t._buffer_size = 1  # Immediate flush for testing
    return t


# ---------------------------------------------------------------------------
# SQLiteSpan Tests
# ---------------------------------------------------------------------------

class TestSQLiteSpan:
    def test_span_creation(self, tracer):
        span = tracer.start_span("test_op")
        assert span.name == "test_op"
        assert span.kind == "INTERNAL"
        assert span.status_code == "UNSET"
        assert span.trace_id
        assert span.span_id
        assert span.parent_span_id is None
        span.end()

    def test_span_attributes(self, tracer):
        span = tracer.start_span("attr_test", attributes={"key1": "val1"})
        span.set_attribute("key2", 42)
        attrs = span.attributes
        assert attrs["key1"] == "val1"
        assert attrs["key2"] == 42
        span.end()

    def test_span_events(self, tracer):
        span = tracer.start_span("event_test")
        span.add_event("my_event", {"detail": "hello"})
        assert len(span._events) == 1
        assert span._events[0]["name"] == "my_event"
        assert span._events[0]["attributes"]["detail"] == "hello"
        span.end()

    def test_span_status(self, tracer):
        span = tracer.start_span("status_test")
        span.set_status("ERROR", "something failed")
        assert span.status_code == "ERROR"
        assert span._status_message == "something failed"
        span.end()

    def test_span_end_sets_duration(self, tracer):
        span = tracer.start_span("duration_test")
        span.end()
        assert span.end_time is not None
        assert span.duration_ms >= 0

    def test_span_double_end_is_safe(self, tracer):
        span = tracer.start_span("double_end")
        span.end()
        end1 = span.end_time
        span.end()  # Should be no-op
        assert span.end_time == end1

    def test_span_no_set_after_end(self, tracer):
        span = tracer.start_span("sealed")
        span.end()
        span.set_attribute("late_key", "val")
        assert "late_key" not in span.attributes
        span.add_event("late_event")
        assert len(span._events) == 0
        span.set_status("OK")
        # Status stays as whatever it was when end() was called
        assert span.status_code == "UNSET"

    def test_span_to_dict(self, tracer):
        span = tracer.start_span("dict_test", attributes={"a": 1})
        span.set_status("OK")
        span.add_event("evt")
        span.end()
        d = span.to_dict()
        assert d["name"] == "dict_test"
        assert d["status_code"] == "OK"
        assert d["attributes"]["a"] == 1
        assert len(d["events"]) == 1

    def test_span_context_manager(self, tracer):
        with tracer.start_span("ctx_mgr") as span:
            span.set_attribute("inside", True)
        # Span ended after with-block
        assert span.end_time is not None


# ---------------------------------------------------------------------------
# SQLiteTracer Tests
# ---------------------------------------------------------------------------

class TestSQLiteTracer:
    def test_tracer_creates_root_span(self, tracer):
        span = tracer.start_span("root")
        assert span.parent_span_id is None
        span.end()

    def test_tracer_inherits_trace_id_from_active(self, tracer):
        parent = tracer.start_span("parent")
        child = tracer.start_span("child")
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        child.end()
        parent.end()

    def test_tracer_explicit_parent(self, tracer):
        p = tracer.start_span("p")
        p.end()
        # Start with explicit parent
        c = tracer.start_span("c", parent=p)
        assert c.trace_id == p.trace_id
        assert c.parent_span_id == p.span_id
        c.end()

    def test_tracer_default_attributes(self, tracer):
        span = tracer.start_span("defaults")
        assert span.attributes.get("icdev.agent_id") == "test-agent"
        assert span.attributes.get("icdev.project_id") == "proj-test"
        span.end()

    def test_get_active_span(self, tracer):
        assert tracer.get_active_span() is None
        span = tracer.start_span("active")
        assert tracer.get_active_span() is span
        span.end()

    def test_flush_writes_to_db(self, db_path, tracer):
        with tracer.start_span("flushed") as span:
            span.set_attribute("flushed", True)

        # buffer_size=1, so flush happened on end()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM otel_spans").fetchall()
        conn.close()
        assert len(rows) >= 1
        row = rows[0]
        assert row["name"] == "flushed"
        attrs = json.loads(row["attributes"])
        assert attrs["flushed"] is True

    def test_flush_empty_is_noop(self, tracer):
        tracer.flush()  # Should not raise

    def test_flush_missing_db(self, tmp_path):
        from tools.observability.sqlite_tracer import SQLiteTracer
        t = SQLiteTracer(db_path=tmp_path / "nonexistent.db")
        t._buffer_size = 1
        with t.start_span("orphan") as span:
            pass
        # Should not raise, spans just discarded

    def test_query_spans_by_trace_id(self, db_path, tracer):
        with tracer.start_span("q1") as s1:
            pass
        tid = s1.trace_id

        results = tracer.query_spans(trace_id=tid)
        assert len(results) >= 1
        assert results[0]["trace_id"] == tid

    def test_query_spans_by_name(self, db_path, tracer):
        with tracer.start_span("unique_name_xyz"):
            pass
        results = tracer.query_spans(name="unique_name_xyz")
        assert len(results) >= 1

    def test_query_spans_by_project(self, db_path, tracer):
        with tracer.start_span("proj_q"):
            pass
        results = tracer.query_spans(project_id="proj-test")
        assert len(results) >= 1

    def test_query_empty_results(self, tracer):
        results = tracer.query_spans(trace_id="nonexistent-id")
        assert results == []

    def test_thread_safety(self, db_path, tracer):
        """Concurrent span creation should not corrupt data."""
        errors = []

        def create_span(i):
            try:
                with tracer.start_span(f"thread_{i}") as span:
                    span.set_attribute("index", i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_span, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        tracer.flush()

    def test_classification_default(self, db_path, tracer):
        with tracer.start_span("classified") as span:
            pass
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT classification FROM otel_spans LIMIT 1").fetchone()
        conn.close()
        assert row[0] == "CUI"

    def test_span_kind_server(self, tracer):
        span = tracer.start_span("server_op", kind="SERVER")
        assert span.kind == "SERVER"
        span.end()
