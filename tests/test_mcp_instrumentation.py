#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for MCP auto-instrumentation (D284).

Verifies that MCP tool calls produce trace spans with correct attributes.
Tests use a mock MCP server base to avoid requiring full MCP infrastructure.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.observability import configure_tracer, get_tracer
from tools.observability.sqlite_tracer import SQLiteTracer


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
def tracer(tmp_db):
    """Configure SQLiteTracer for tests."""
    t = SQLiteTracer(db_path=tmp_db, agent_id="test-agent")
    t._buffer_size = 1  # Immediate flush
    configure_tracer(t)
    return t


class TestMCPInstrumentation:
    """Test MCP base_server tool call tracing (D284)."""

    def test_tool_call_creates_span(self, tracer, tmp_db):
        """Verify that _handle_tools_call creates a trace span."""
        from tools.mcp.base_server import MCPServer

        server = MCPServer("test-server")
        server.server_name = "test-server"

        def echo_handler(args):
            return {"echo": args.get("msg", "")}

        server.register_tool("echo", "Echo tool", {"type": "object"}, echo_handler)

        result = server._handle_tools_call({"name": "echo", "arguments": {"msg": "hello"}})
        tracer.flush()

        assert result["isError"] is False

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM otel_spans WHERE name='mcp.tool_call'").fetchall()
        conn.close()

        assert len(rows) >= 1
        attrs = json.loads(rows[0]["attributes"])
        assert attrs["mcp.tool.name"] == "echo"
        assert attrs["mcp.server.name"] == "test-server"
        assert "mcp.tool.args_hash" in attrs
        assert rows[0]["status_code"] == "OK"

    def test_tool_call_error_span(self, tracer, tmp_db):
        """Verify error spans are created for failing tool calls."""
        from tools.mcp.base_server import MCPServer

        server = MCPServer("test-server")

        def failing_handler(args):
            raise ValueError("deliberate failure")

        server.register_tool("fail", "Failing tool", {"type": "object"}, failing_handler)

        result = server._handle_tools_call({"name": "fail", "arguments": {}})
        tracer.flush()

        assert result["isError"] is True

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM otel_spans WHERE name='mcp.tool_call'").fetchall()
        conn.close()

        assert len(rows) >= 1
        assert rows[0]["status_code"] == "ERROR"

    def test_tool_call_result_hash(self, tracer, tmp_db):
        """Verify result hash is recorded on successful calls."""
        from tools.mcp.base_server import MCPServer

        server = MCPServer("hash-server")
        server.server_name = "hash-server"

        def data_handler(args):
            return {"data": "sensitive"}

        server.register_tool("data", "Data tool", {"type": "object"}, data_handler)
        server._handle_tools_call({"name": "data", "arguments": {}})
        tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM otel_spans WHERE name='mcp.tool_call'").fetchone()
        conn.close()

        attrs = json.loads(row["attributes"])
        assert "mcp.tool.result_hash" in attrs
        assert len(attrs["mcp.tool.result_hash"]) == 16

    def test_span_kind_is_server(self, tracer, tmp_db):
        """MCP tool call spans should have SERVER kind."""
        from tools.mcp.base_server import MCPServer

        server = MCPServer("kind-server")

        def noop(args):
            return "ok"

        server.register_tool("noop", "Noop", {"type": "object"}, noop)
        server._handle_tools_call({"name": "noop", "arguments": {}})
        tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM otel_spans").fetchone()
        conn.close()

        assert row["kind"] == "SERVER"

    def test_multiple_tool_calls(self, tracer, tmp_db):
        """Multiple tool calls create separate spans."""
        from tools.mcp.base_server import MCPServer

        server = MCPServer("multi-server")

        def handler(args):
            return "result"

        server.register_tool("tool_a", "Tool A", {"type": "object"}, handler)
        server.register_tool("tool_b", "Tool B", {"type": "object"}, handler)

        server._handle_tools_call({"name": "tool_a", "arguments": {}})
        server._handle_tools_call({"name": "tool_b", "arguments": {}})
        tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT * FROM otel_spans WHERE name='mcp.tool_call'").fetchall()
        conn.close()

        assert len(rows) >= 2

    def test_genai_operation_attribute(self, tracer, tmp_db):
        """Verify gen_ai.operation.name is set to execute_tool."""
        from tools.mcp.base_server import MCPServer

        server = MCPServer("genai-server")

        def handler(args):
            return {}

        server.register_tool("t", "T", {"type": "object"}, handler)
        server._handle_tools_call({"name": "t", "arguments": {}})
        tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM otel_spans").fetchone()
        conn.close()

        attrs = json.loads(row["attributes"])
        assert attrs["gen_ai.operation.name"] == "execute_tool"


class TestLLMRouterInstrumentation:
    """Test LLM router tracing (D286) — mock provider only."""

    def test_successful_invoke_creates_span(self, tracer, tmp_db):
        """Verify successful LLM invocation creates a span with GenAI attributes."""
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest, LLMResponse

        mock_response = LLMResponse(
            content="test response",
            model_id="test-model",
            input_tokens=10,
            output_tokens=20,
        )

        router = LLMRouter.__new__(LLMRouter)
        router._config = {}
        router._providers = {}
        router._availability_cache = {}
        router._injection_cache = {}
        router._injection_detector = None

        with patch.object(router, "_scan_for_injection", return_value="allow"), \
             patch.object(router, "get_effort", return_value="medium"), \
             patch.object(router, "_get_chain_for_function", return_value=["test-model"]), \
             patch.object(router, "_get_model_config", return_value={"provider": "test", "model_id": "m1"}), \
             patch.object(router, "_get_provider") as mock_prov:

            mock_provider = MagicMock()
            mock_provider.invoke.return_value = mock_response
            mock_prov.return_value = mock_provider

            request = LLMRequest(messages=[{"role": "user", "content": "hello"}], effort="medium")
            response = router.invoke("code_generation", request)

        tracer.flush()

        assert response.content == "test response"

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM otel_spans WHERE name='gen_ai.invoke'").fetchall()
        conn.close()

        assert len(rows) >= 1
        attrs = json.loads(rows[0]["attributes"])
        assert attrs["gen_ai.system"] == "test"
        assert attrs["gen_ai.request.model"] == "m1"
        assert rows[0]["status_code"] == "OK"

    def test_failed_invoke_error_span(self, tracer, tmp_db):
        """Verify failed LLM invocation creates an error span."""
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter.__new__(LLMRouter)
        router._config = {}
        router._providers = {}
        router._availability_cache = {}
        router._injection_cache = {}
        router._injection_detector = None

        with patch.object(router, "_scan_for_injection", return_value="allow"), \
             patch.object(router, "get_effort", return_value="medium"), \
             patch.object(router, "_get_chain_for_function", return_value=["m1"]), \
             patch.object(router, "_get_model_config", return_value={"provider": "fail_prov", "model_id": "m1"}), \
             patch.object(router, "_get_provider") as mock_prov:

            mock_provider = MagicMock()
            mock_provider.invoke.side_effect = RuntimeError("provider down")
            mock_prov.return_value = mock_provider

            request = LLMRequest(messages=[{"role": "user", "content": "hello"}], effort="medium")
            with pytest.raises(RuntimeError):
                router.invoke("code_generation", request)

        tracer.flush()

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM otel_spans WHERE name='gen_ai.invoke'").fetchall()
        conn.close()

        assert len(rows) >= 1
        assert rows[0]["status_code"] == "ERROR"
