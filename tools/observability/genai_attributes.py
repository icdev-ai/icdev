#!/usr/bin/env python3
# CUI // SP-CTI
"""OTel GenAI Semantic Convention constants (D286).

Provides standard attribute keys for instrumenting LLM and agent operations
following the OpenTelemetry GenAI semantic conventions.

Reference:
    https://opentelemetry.io/docs/specs/semconv/gen-ai/
    https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/

These constants are used by:
  - tools/observability/instrumentation.py (auto-decorators)
  - tools/mcp/base_server.py (MCP tool span, D284)
  - tools/llm/router.py (LLM invoke span, D286)
  - tools/a2a/agent_client.py (A2A span, D285)
"""

# --- GenAI Operation ---
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"  # e.g., "chat", "execute_tool"

# --- GenAI System ---
GEN_AI_SYSTEM = "gen_ai.system"  # e.g., "aws.bedrock", "anthropic", "openai"

# --- GenAI Agent ---
GEN_AI_AGENT_ID = "gen_ai.agent.id"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_DESCRIPTION = "gen_ai.agent.description"

# --- GenAI Request ---
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"

# --- GenAI Response ---
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reasons"

# --- GenAI Usage (Token Counts) ---
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"

# --- ICDEV Extensions (not in OTel spec) ---
GEN_AI_USAGE_THINKING_TOKENS = "gen_ai.usage.thinking_tokens"
GEN_AI_USAGE_COST_USD = "gen_ai.usage.cost_usd"
GEN_AI_LATENCY_MS = "gen_ai.latency_ms"
GEN_AI_RETRY_COUNT = "gen_ai.retry_count"
GEN_AI_EFFORT = "gen_ai.effort"

# --- MCP Tool Call Attributes (D284) ---
MCP_TOOL_NAME = "mcp.tool.name"
MCP_SERVER_NAME = "mcp.server.name"
MCP_TOOL_ARGS_HASH = "mcp.tool.args_hash"
MCP_TOOL_RESULT_HASH = "mcp.tool.result_hash"
MCP_TOOL_ERROR = "mcp.tool.error"

# --- A2A Attributes (D285) ---
A2A_SOURCE_AGENT = "a2a.source_agent"
A2A_TARGET_AGENT = "a2a.target_agent"
A2A_METHOD = "a2a.method"
A2A_TASK_ID = "a2a.task_id"

# --- ICDEV Metadata ---
ICDEV_PROJECT_ID = "icdev.project_id"
ICDEV_AGENT_ID = "icdev.agent_id"
ICDEV_CLASSIFICATION = "icdev.classification"
ICDEV_CORRELATION_ID = "icdev.correlation_id"

# --- Span Names ---
SPAN_MCP_TOOL_CALL = "mcp.tool_call"
SPAN_LLM_INVOKE = "gen_ai.invoke"
SPAN_A2A_REQUEST = "a2a.request"
SPAN_A2A_HANDLE = "a2a.handle"
SPAN_BEDROCK_INVOKE = "gen_ai.bedrock.invoke"

# --- Span Kinds ---
KIND_INTERNAL = "INTERNAL"
KIND_CLIENT = "CLIENT"
KIND_SERVER = "SERVER"
KIND_PRODUCER = "PRODUCER"
KIND_CONSUMER = "CONSUMER"

# --- Status Codes ---
STATUS_UNSET = "UNSET"
STATUS_OK = "OK"
STATUS_ERROR = "ERROR"
