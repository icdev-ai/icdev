#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/observability/genai_attributes.py — OTel GenAI constants (D286).

Covers:
  - All constant categories exist and follow naming conventions
  - GenAI operation, system, agent constants
  - GenAI request/response/usage constants
  - ICDEV extension constants
  - MCP tool call constants (D284)
  - A2A constants (D285)
  - Span names and kinds
  - Status codes
  - No duplicate values across categories
"""

import pytest

from tools.observability import genai_attributes as ga


# ---------------------------------------------------------------------------
# GenAI Core Constants
# ---------------------------------------------------------------------------

class TestGenAIOperation:
    def test_operation_name(self):
        assert ga.GEN_AI_OPERATION_NAME == "gen_ai.operation.name"

    def test_system(self):
        assert ga.GEN_AI_SYSTEM == "gen_ai.system"


class TestGenAIAgent:
    def test_agent_id(self):
        assert ga.GEN_AI_AGENT_ID == "gen_ai.agent.id"

    def test_agent_name(self):
        assert ga.GEN_AI_AGENT_NAME == "gen_ai.agent.name"

    def test_agent_description(self):
        assert ga.GEN_AI_AGENT_DESCRIPTION == "gen_ai.agent.description"


class TestGenAIRequest:
    def test_model(self):
        assert ga.GEN_AI_REQUEST_MODEL == "gen_ai.request.model"

    def test_temperature(self):
        assert ga.GEN_AI_REQUEST_TEMPERATURE == "gen_ai.request.temperature"

    def test_max_tokens(self):
        assert ga.GEN_AI_REQUEST_MAX_TOKENS == "gen_ai.request.max_tokens"

    def test_top_p(self):
        assert ga.GEN_AI_REQUEST_TOP_P == "gen_ai.request.top_p"


class TestGenAIResponse:
    def test_response_model(self):
        assert ga.GEN_AI_RESPONSE_MODEL == "gen_ai.response.model"

    def test_finish_reason(self):
        assert ga.GEN_AI_RESPONSE_FINISH_REASON == "gen_ai.response.finish_reasons"


class TestGenAIUsage:
    def test_input_tokens(self):
        assert ga.GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"

    def test_output_tokens(self):
        assert ga.GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"

    def test_total_tokens(self):
        assert ga.GEN_AI_USAGE_TOTAL_TOKENS == "gen_ai.usage.total_tokens"


# ---------------------------------------------------------------------------
# ICDEV Extensions
# ---------------------------------------------------------------------------

class TestICDEVExtensions:
    def test_thinking_tokens(self):
        assert ga.GEN_AI_USAGE_THINKING_TOKENS == "gen_ai.usage.thinking_tokens"

    def test_cost_usd(self):
        assert ga.GEN_AI_USAGE_COST_USD == "gen_ai.usage.cost_usd"

    def test_latency_ms(self):
        assert ga.GEN_AI_LATENCY_MS == "gen_ai.latency_ms"

    def test_retry_count(self):
        assert ga.GEN_AI_RETRY_COUNT == "gen_ai.retry_count"

    def test_effort(self):
        assert ga.GEN_AI_EFFORT == "gen_ai.effort"


# ---------------------------------------------------------------------------
# MCP Tool Call (D284)
# ---------------------------------------------------------------------------

class TestMCPAttributes:
    def test_tool_name(self):
        assert ga.MCP_TOOL_NAME == "mcp.tool.name"

    def test_server_name(self):
        assert ga.MCP_SERVER_NAME == "mcp.server.name"

    def test_args_hash(self):
        assert ga.MCP_TOOL_ARGS_HASH == "mcp.tool.args_hash"

    def test_result_hash(self):
        assert ga.MCP_TOOL_RESULT_HASH == "mcp.tool.result_hash"

    def test_error(self):
        assert ga.MCP_TOOL_ERROR == "mcp.tool.error"


# ---------------------------------------------------------------------------
# A2A (D285)
# ---------------------------------------------------------------------------

class TestA2AAttributes:
    def test_source_agent(self):
        assert ga.A2A_SOURCE_AGENT == "a2a.source_agent"

    def test_target_agent(self):
        assert ga.A2A_TARGET_AGENT == "a2a.target_agent"

    def test_method(self):
        assert ga.A2A_METHOD == "a2a.method"

    def test_task_id(self):
        assert ga.A2A_TASK_ID == "a2a.task_id"


# ---------------------------------------------------------------------------
# ICDEV Metadata
# ---------------------------------------------------------------------------

class TestICDEVMetadata:
    def test_project_id(self):
        assert ga.ICDEV_PROJECT_ID == "icdev.project_id"

    def test_agent_id(self):
        assert ga.ICDEV_AGENT_ID == "icdev.agent_id"

    def test_classification(self):
        assert ga.ICDEV_CLASSIFICATION == "icdev.classification"

    def test_correlation_id(self):
        assert ga.ICDEV_CORRELATION_ID == "icdev.correlation_id"


# ---------------------------------------------------------------------------
# Span Names
# ---------------------------------------------------------------------------

class TestSpanNames:
    def test_mcp_tool_call(self):
        assert ga.SPAN_MCP_TOOL_CALL == "mcp.tool_call"

    def test_llm_invoke(self):
        assert ga.SPAN_LLM_INVOKE == "gen_ai.invoke"

    def test_a2a_request(self):
        assert ga.SPAN_A2A_REQUEST == "a2a.request"

    def test_a2a_handle(self):
        assert ga.SPAN_A2A_HANDLE == "a2a.handle"

    def test_bedrock_invoke(self):
        assert ga.SPAN_BEDROCK_INVOKE == "gen_ai.bedrock.invoke"


# ---------------------------------------------------------------------------
# Span Kinds
# ---------------------------------------------------------------------------

class TestSpanKinds:
    def test_internal(self):
        assert ga.KIND_INTERNAL == "INTERNAL"

    def test_client(self):
        assert ga.KIND_CLIENT == "CLIENT"

    def test_server(self):
        assert ga.KIND_SERVER == "SERVER"

    def test_producer(self):
        assert ga.KIND_PRODUCER == "PRODUCER"

    def test_consumer(self):
        assert ga.KIND_CONSUMER == "CONSUMER"


# ---------------------------------------------------------------------------
# Status Codes
# ---------------------------------------------------------------------------

class TestStatusCodes:
    def test_unset(self):
        assert ga.STATUS_UNSET == "UNSET"

    def test_ok(self):
        assert ga.STATUS_OK == "OK"

    def test_error(self):
        assert ga.STATUS_ERROR == "ERROR"


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------

class TestNamingConventions:
    def test_genai_prefix(self):
        """All GenAI constants should use gen_ai.* prefix."""
        genai_attrs = [
            ga.GEN_AI_OPERATION_NAME, ga.GEN_AI_SYSTEM,
            ga.GEN_AI_AGENT_ID, ga.GEN_AI_AGENT_NAME,
            ga.GEN_AI_REQUEST_MODEL, ga.GEN_AI_REQUEST_TEMPERATURE,
            ga.GEN_AI_RESPONSE_MODEL, ga.GEN_AI_RESPONSE_FINISH_REASON,
            ga.GEN_AI_USAGE_INPUT_TOKENS, ga.GEN_AI_USAGE_OUTPUT_TOKENS,
        ]
        for attr in genai_attrs:
            assert attr.startswith("gen_ai."), f"{attr} missing gen_ai. prefix"

    def test_mcp_prefix(self):
        """All MCP constants should use mcp.* prefix."""
        mcp_attrs = [ga.MCP_TOOL_NAME, ga.MCP_SERVER_NAME, ga.MCP_TOOL_ARGS_HASH]
        for attr in mcp_attrs:
            assert attr.startswith("mcp."), f"{attr} missing mcp. prefix"

    def test_a2a_prefix(self):
        """All A2A constants should use a2a.* prefix."""
        a2a_attrs = [ga.A2A_SOURCE_AGENT, ga.A2A_TARGET_AGENT, ga.A2A_METHOD]
        for attr in a2a_attrs:
            assert attr.startswith("a2a."), f"{attr} missing a2a. prefix"

    def test_no_duplicate_values(self):
        """All constant values should be unique."""
        import inspect
        members = inspect.getmembers(ga)
        values = []
        for name, val in members:
            if name.isupper() and isinstance(val, str):
                values.append(val)
        assert len(values) == len(set(values)), "Duplicate constant values found"
