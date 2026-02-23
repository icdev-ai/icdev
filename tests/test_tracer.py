#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ICDEV Observability — Tracer ABCs, SQLiteTracer, ProxyTracer (D280).

Covers:
  - NullTracer/NullSpan (zero overhead)
  - SQLiteTracer with in-memory DB
  - ProxyTracer delegation
  - Span hierarchy (parent/child)
  - Content tag gating (D282)
  - Span attributes and events
  - Context manager usage
  - Error span status
  - @traced() decorator
"""

import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure we can import from project root
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.observability.tracer import (
    NullSpan,
    NullTracer,
    ProxyTracer,
    Span,
    Tracer,
    set_content_tag,
)
from tools.observability.sqlite_tracer import SQLiteSpan, SQLiteTracer


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite DB with otel_spans table."""
    db_path = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
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
        );
    """)
    conn.close()
    return db_path


@pytest.fixture
def sqlite_tracer(tmp_db):
    """Create a SQLiteTracer with test DB."""
    tracer = SQLiteTracer(
        db_path=tmp_db,
        agent_id="test-agent",
        project_id="test-project",
    )
    # Set buffer to 1 for immediate flush in tests
    tracer._buffer_size = 1
    return tracer


# ============================================================
# NullTracer / NullSpan Tests
# ============================================================

class TestNullSpan:
    def test_null_span_properties(self):
        span = NullSpan()
        assert len(span.span_id) > 0
        assert len(span.trace_id) > 0
        assert span.parent_span_id is None

    def test_null_span_operations_are_noop(self):
        span = NullSpan()
        span.set_attribute("key", "value")
        span.add_event("test_event", {"data": 123})
        span.set_status("OK")
        span.end()
        # No errors, just no-ops

    def test_null_span_context_manager(self):
        span = NullSpan()
        with span as s:
            s.set_attribute("inside", True)
        # Span should have OK status after clean exit

    def test_null_span_context_manager_with_error(self):
        span = NullSpan()
        with pytest.raises(ValueError):
            with span:
                raise ValueError("test error")

    def test_null_span_inherits_trace_id(self):
        span = NullSpan(trace_id="abc123", span_id="def456")
        assert span.trace_id == "abc123"
        assert span.span_id == "def456"


class TestNullTracer:
    def test_start_span_returns_null_span(self):
        tracer = NullTracer()
        span = tracer.start_span("test")
        assert isinstance(span, NullSpan)

    def test_start_span_inherits_parent_trace_id(self):
        tracer = NullTracer()
        parent = tracer.start_span("parent")
        child = tracer.start_span("child", parent=parent)
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id

    def test_get_active_span_returns_none(self):
        tracer = NullTracer()
        assert tracer.get_active_span() is None

    def test_flush_is_noop(self):
        tracer = NullTracer()
        tracer.flush()  # Should not raise


# ============================================================
# ProxyTracer Tests
# ============================================================

class TestProxyTracer:
    def test_default_is_null_tracer(self):
        proxy = ProxyTracer()
        assert isinstance(proxy.actual, NullTracer)

    def test_set_tracer_swaps_backend(self, sqlite_tracer):
        proxy = ProxyTracer()
        proxy.set_tracer(sqlite_tracer)
        assert isinstance(proxy.actual, SQLiteTracer)

    def test_delegates_start_span(self, sqlite_tracer):
        proxy = ProxyTracer()
        proxy.set_tracer(sqlite_tracer)
        span = proxy.start_span("test_op")
        assert isinstance(span, SQLiteSpan)
        assert span.name == "test_op"
        span.end()

    def test_delegates_flush(self, sqlite_tracer):
        proxy = ProxyTracer()
        proxy.set_tracer(sqlite_tracer)
        proxy.flush()  # Should call sqlite_tracer.flush()

    def test_delegates_get_active_span(self, sqlite_tracer):
        proxy = ProxyTracer()
        proxy.set_tracer(sqlite_tracer)
        # No active span yet
        assert proxy.get_active_span() is None


# ============================================================
# SQLiteTracer Tests
# ============================================================

class TestSQLiteTracer:
    def test_start_span_returns_sqlite_span(self, sqlite_tracer):
        span = sqlite_tracer.start_span("test_op")
        assert isinstance(span, SQLiteSpan)
        assert span.name == "test_op"
        span.end()

    def test_span_has_trace_id(self, sqlite_tracer):
        span = sqlite_tracer.start_span("test_op")
        assert len(span.trace_id) == 32  # UUID hex
        assert len(span.span_id) == 16   # 16-char hex
        span.end()

    def test_span_inherits_parent(self, sqlite_tracer):
        parent = sqlite_tracer.start_span("parent_op")
        child = sqlite_tracer.start_span("child_op", parent=parent)
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        child.end()
        parent.end()

    def test_span_set_attribute(self, sqlite_tracer):
        span = sqlite_tracer.start_span("test_op")
        span.set_attribute("custom_key", "custom_value")
        assert span.attributes["custom_key"] == "custom_value"
        span.end()

    def test_span_add_event(self, sqlite_tracer):
        span = sqlite_tracer.start_span("test_op")
        span.add_event("my_event", {"detail": "abc"})
        assert len(span._events) == 1
        assert span._events[0]["name"] == "my_event"
        span.end()

    def test_span_set_status(self, sqlite_tracer):
        span = sqlite_tracer.start_span("test_op")
        span.set_status("ERROR", "something failed")
        assert span.status_code == "ERROR"
        span.end()

    def test_span_context_manager_ok(self, sqlite_tracer):
        with sqlite_tracer.start_span("test_op") as span:
            span.set_attribute("step", 1)
        assert span.status_code == "OK"
        assert span.end_time is not None
        assert span.duration_ms >= 0

    def test_span_context_manager_error(self, sqlite_tracer):
        with pytest.raises(RuntimeError):
            with sqlite_tracer.start_span("fail_op") as span:
                raise RuntimeError("boom")
        assert span.status_code == "ERROR"

    def test_span_persisted_to_db(self, sqlite_tracer, tmp_db):
        with sqlite_tracer.start_span("persist_test") as span:
            span.set_attribute("test_key", "test_val")
        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM otel_spans").fetchall()
        conn.close()

        assert len(rows) >= 1
        row = rows[0]
        assert row["name"] == "persist_test"
        assert row["status_code"] == "OK"
        attrs = json.loads(row["attributes"])
        assert attrs["test_key"] == "test_val"

    def test_span_default_attributes(self, sqlite_tracer, tmp_db):
        with sqlite_tracer.start_span("default_attr_test") as span:
            pass
        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM otel_spans WHERE name='default_attr_test'").fetchone()
        conn.close()

        attrs = json.loads(row["attributes"])
        assert attrs["icdev.agent_id"] == "test-agent"
        assert attrs["icdev.project_id"] == "test-project"

    def test_nested_spans_hierarchy(self, sqlite_tracer, tmp_db):
        with sqlite_tracer.start_span("root") as root:
            with sqlite_tracer.start_span("child") as child:
                pass
        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        root_row = conn.execute("SELECT * FROM otel_spans WHERE name='root'").fetchone()
        child_row = conn.execute("SELECT * FROM otel_spans WHERE name='child'").fetchone()
        conn.close()

        assert child_row["trace_id"] == root_row["trace_id"]
        assert child_row["parent_span_id"] == root_row["id"]

    def test_query_spans_by_trace_id(self, sqlite_tracer, tmp_db):
        with sqlite_tracer.start_span("query_test") as span:
            trace_id = span.trace_id
        sqlite_tracer.flush()

        results = sqlite_tracer.query_spans(trace_id=trace_id)
        assert len(results) >= 1
        assert results[0]["trace_id"] == trace_id

    def test_query_spans_by_project_id(self, sqlite_tracer, tmp_db):
        with sqlite_tracer.start_span("proj_query") as span:
            pass
        sqlite_tracer.flush()

        results = sqlite_tracer.query_spans(project_id="test-project")
        assert len(results) >= 1

    def test_flush_empty_buffer(self, sqlite_tracer):
        # Should not raise
        sqlite_tracer.flush()

    def test_span_to_dict(self, sqlite_tracer):
        with sqlite_tracer.start_span("dict_test") as span:
            span.set_attribute("key", "val")

        d = span.to_dict()
        assert d["name"] == "dict_test"
        assert d["status_code"] == "OK"
        assert d["attributes"]["key"] == "val"
        assert d["trace_id"] is not None

    def test_span_kind(self, sqlite_tracer):
        span = sqlite_tracer.start_span("client_op", kind="CLIENT")
        assert span.kind == "CLIENT"
        span.end()

    def test_span_not_modified_after_end(self, sqlite_tracer):
        span = sqlite_tracer.start_span("ended_test")
        span.end()
        span.set_attribute("after_end", True)
        assert "after_end" not in span.attributes

    def test_double_end_is_safe(self, sqlite_tracer):
        span = sqlite_tracer.start_span("double_end")
        span.end()
        span.end()  # Should not raise or duplicate


# ============================================================
# Content Tag Gating Tests (D282)
# ============================================================

class TestContentTagGating:
    def test_set_content_tag_hash_only_by_default(self):
        span = NullSpan()
        with patch.dict(os.environ, {}, clear=True):
            set_content_tag(span, "gen_ai.prompt", "Hello world")
        # NullSpan discards attributes, but the function should not raise

    def test_set_content_tag_with_sqlite_span(self, sqlite_tracer):
        span = sqlite_tracer.start_span("content_test")

        with patch.dict(os.environ, {"ICDEV_CONTENT_TRACING_ENABLED": "false"}):
            set_content_tag(span, "gen_ai.prompt", "Hello world")

        assert "gen_ai.prompt_hash" in span.attributes
        assert "gen_ai.prompt" not in span.attributes
        span.end()

    def test_set_content_tag_plaintext_when_enabled(self, sqlite_tracer):
        span = sqlite_tracer.start_span("content_enabled_test")

        with patch.dict(os.environ, {"ICDEV_CONTENT_TRACING_ENABLED": "true"}):
            set_content_tag(span, "gen_ai.prompt", "Hello world")

        assert "gen_ai.prompt_hash" in span.attributes
        assert span.attributes["gen_ai.prompt"] == "Hello world"
        span.end()

    def test_content_hash_is_sha256(self, sqlite_tracer):
        import hashlib
        span = sqlite_tracer.start_span("hash_test")
        text = "test content for hashing"
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        with patch.dict(os.environ, {"ICDEV_CONTENT_TRACING_ENABLED": "false"}):
            set_content_tag(span, "my.field", text)

        assert span.attributes["my.field_hash"] == expected_hash
        span.end()


# ============================================================
# @traced() Decorator Tests
# ============================================================

class TestTracedDecorator:
    def test_traced_wraps_function(self, sqlite_tracer, tmp_db):
        from tools.observability import configure_tracer
        from tools.observability.instrumentation import traced

        configure_tracer(sqlite_tracer)

        @traced(name="test.decorated")
        def my_function(x, y):
            return x + y

        result = my_function(1, 2)
        assert result == 3
        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT * FROM otel_spans WHERE name='test.decorated'").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_traced_captures_error(self, sqlite_tracer, tmp_db):
        from tools.observability import configure_tracer
        from tools.observability.instrumentation import traced

        configure_tracer(sqlite_tracer)

        @traced(name="test.error_func")
        def failing_function():
            raise ValueError("deliberate error")

        with pytest.raises(ValueError):
            failing_function()

        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM otel_spans WHERE name='test.error_func'").fetchone()
        conn.close()

        assert row["status_code"] == "ERROR"

    def test_traced_auto_names_from_function(self, sqlite_tracer, tmp_db):
        from tools.observability import configure_tracer
        from tools.observability.instrumentation import traced

        configure_tracer(sqlite_tracer)

        @traced()
        def auto_named():
            return True

        auto_named()
        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT name FROM otel_spans").fetchall()
        conn.close()
        # Should contain the module.qualname
        assert any("auto_named" in row[0] for row in rows)

    def test_traced_with_static_attributes(self, sqlite_tracer, tmp_db):
        from tools.observability import configure_tracer
        from tools.observability.instrumentation import traced

        configure_tracer(sqlite_tracer)

        @traced(name="test.with_attrs", attributes={"server": "builder"})
        def func_with_attrs():
            return 42

        func_with_attrs()
        sqlite_tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM otel_spans WHERE name='test.with_attrs'").fetchone()
        conn.close()
        attrs = json.loads(row["attributes"])
        assert attrs["server"] == "builder"


# ============================================================
# Module-Level get_tracer / enable_tracing Tests
# ============================================================

class TestModuleLevelAPI:
    def test_get_tracer_returns_proxy(self):
        from tools.observability import get_tracer
        tracer = get_tracer()
        assert isinstance(tracer, ProxyTracer)

    def test_enable_tracing_null(self):
        from tools.observability import enable_tracing
        tracer = enable_tracing("null")
        assert isinstance(tracer, NullTracer)

    def test_enable_tracing_sqlite(self, tmp_db):
        from tools.observability import enable_tracing, get_tracer
        with patch.object(
            SQLiteTracer, '__init__',
            lambda self, **kw: (
                setattr(self, '_db_path', tmp_db) or
                setattr(self, '_agent_id', None) or
                setattr(self, '_project_id', None) or
                setattr(self, '_classification', 'CUI') or
                setattr(self, '_write_lock', __import__('threading').Lock()) or
                setattr(self, '_buffer', []) or
                setattr(self, '_buffer_size', 10)
            )
        ):
            tracer = enable_tracing("sqlite")
            assert isinstance(tracer, SQLiteTracer)
