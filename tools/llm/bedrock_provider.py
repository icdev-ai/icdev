# CUI // SP-CTI
"""AWS Bedrock LLM Provider.

Implements the LLMProvider interface for Amazon Bedrock.
Supports Anthropic models on Bedrock with thinking/effort, tools,
structured output, and model fallback with retry/backoff.
"""

import json
import logging
import os
import random
import time
from typing import Any, Dict, Iterator

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    messages_to_anthropic,
    tools_to_anthropic,
)

logger = logging.getLogger("icdev.llm.bedrock")

try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    ClientError = Exception
    HAS_BOTO3 = False

# Retry configuration
MAX_RETRIES = 5
BASE_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 30.0
RETRYABLE_ERROR_CODES = [
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
    "TooManyRequestsException",
]


class BedrockLLMProvider(LLMProvider):
    """AWS Bedrock provider supporting Anthropic models.

    Handles Anthropic-specific request/response format, adaptive
    thinking, effort parameters, tool use, and structured output.
    """

    def __init__(self, region: str = None):
        self._region = region or os.environ.get("AWS_DEFAULT_REGION", "us-gov-west-1")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "bedrock"

    def _get_client(self):
        """Lazy-init boto3 bedrock-runtime client."""
        if self._client is None:
            if not HAS_BOTO3:
                raise ImportError(
                    "boto3 is required for Bedrock. Install: pip install boto3"
                )
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    @staticmethod
    def _effort_to_budget(effort: str, max_tokens: int) -> int:
        """Map effort level to thinking budget_tokens."""
        ratios = {
            "low": (0.10, 1024),
            "medium": (0.25, 4096),
            "high": (0.60, 10240),
            "max": (1.0, 10240),
        }
        ratio, floor_val = ratios.get(effort, (0.25, 4096))
        return max(int(max_tokens * ratio), floor_val)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Check if exception is retryable."""
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if error_code in RETRYABLE_ERROR_CODES:
            return True
        exc_name = type(exc).__name__
        return exc_name in ("ReadTimeoutError", "ConnectTimeoutError", "ConnectionError")

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff with jitter."""
        delay = min(MAX_RETRY_DELAY, BASE_RETRY_DELAY * (2 ** attempt))
        return delay * random.uniform(0.5, 1.0)

    def _build_body(self, request: LLMRequest, model_id: str,
                    model_config: dict) -> dict:
        """Build Anthropic-format request body for Bedrock."""
        max_output = model_config.get("max_output_tokens", 8192)
        effective_max = min(request.max_tokens, max_output)

        # Convert messages to Anthropic format
        messages = messages_to_anthropic(request.messages)

        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": effective_max,
            "messages": messages,
        }

        if request.system_prompt:
            body["system"] = [{"type": "text", "text": request.system_prompt}]

        if request.temperature is not None:
            body["temperature"] = request.temperature

        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        # Adaptive thinking (Opus 4.6 / Sonnet 4.5)
        if model_config.get("supports_thinking", False):
            effort = request.effort or "medium"
            body["thinking"] = {
                "type": "adaptive",
                "budget_tokens": self._effort_to_budget(effort, effective_max),
            }

        # Tools
        if request.tools:
            body["tools"] = tools_to_anthropic(request.tools)

        # Structured output
        if request.output_schema and model_config.get("supports_structured_output", False):
            body["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "json_schema": request.output_schema,
                }
            }

        return body

    def _parse_response(self, response_body: dict, model_id: str) -> LLMResponse:
        """Parse Anthropic response body into LLMResponse."""
        resp = LLMResponse(provider=self.provider_name)
        resp.model_id = model_id
        resp.stop_reason = response_body.get("stop_reason", "")

        usage = response_body.get("usage", {})
        resp.input_tokens = usage.get("input_tokens", 0)
        resp.output_tokens = usage.get("output_tokens", 0)

        content_blocks = response_body.get("content", [])
        text_parts = []
        tool_calls = []

        for block in content_blocks:
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                })
            elif btype == "thinking":
                resp.thinking_tokens += block.get("tokens", 0)

        resp.content = "\n".join(text_parts)
        resp.tool_calls = tool_calls

        # Try parsing structured output
        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def invoke(self, request: LLMRequest, model_id: str,
               model_config: dict) -> LLMResponse:
        """Invoke Bedrock synchronously with retry."""
        body = self._build_body(request, model_id, model_config)
        client = self._get_client()
        start_time = time.time()

        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(body),
                )
                response_body = json.loads(raw["body"].read())
                resp = self._parse_response(response_body, model_id)
                resp.duration_ms = int((time.time() - start_time) * 1000)
                resp.classification = request.classification
                return resp
            except Exception as exc:
                if self._is_retryable(exc) and attempt < MAX_RETRIES:
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        "Bedrock retry %d/%d: %s — %.1fs",
                        attempt + 1, MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    raise

        raise RuntimeError("Bedrock invocation failed after retries")

    def invoke_streaming(self, request: LLMRequest, model_id: str,
                         model_config: dict) -> Iterator[dict]:
        """Invoke Bedrock with streaming response."""
        body = self._build_body(request, model_id, model_config)
        client = self._get_client()
        start_time = time.time()

        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = client.invoke_model_with_response_stream(
                    modelId=model_id,
                    body=json.dumps(body),
                )
                stream = raw.get("body", [])
                total_input = 0
                total_output = 0

                for event in stream:
                    chunk = event.get("chunk")
                    if not chunk:
                        continue
                    chunk_data = json.loads(chunk["bytes"])
                    etype = chunk_data.get("type", "")

                    if etype == "message_start":
                        msg = chunk_data.get("message", {})
                        usage = msg.get("usage", {})
                        total_input += usage.get("input_tokens", 0)
                        yield {"type": "message_start", "message": msg}

                    elif etype == "content_block_start":
                        block = chunk_data.get("content_block", {})
                        btype = block.get("type", "")
                        if btype == "tool_use":
                            yield {
                                "type": "tool_use_start",
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                            }
                        elif btype == "thinking":
                            yield {"type": "thinking_start"}

                    elif etype == "content_block_delta":
                        delta = chunk_data.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta":
                            yield {"type": "text", "text": delta.get("text", "")}
                        elif dtype == "thinking_delta":
                            yield {"type": "thinking", "thinking": delta.get("thinking", "")}
                        elif dtype == "input_json_delta":
                            yield {"type": "tool_use_input", "partial_json": delta.get("partial_json", "")}

                    elif etype == "content_block_stop":
                        yield {"type": "content_block_stop"}

                    elif etype == "message_delta":
                        delta = chunk_data.get("delta", {})
                        usage = chunk_data.get("usage", {})
                        total_output += usage.get("output_tokens", 0)
                        yield {
                            "type": "message_delta",
                            "stop_reason": delta.get("stop_reason", ""),
                            "usage": {"input_tokens": total_input, "output_tokens": total_output},
                        }

                    elif etype == "message_stop":
                        yield {
                            "type": "message_stop",
                            "model_id": model_id,
                            "duration_ms": int((time.time() - start_time) * 1000),
                        }

                return

            except Exception as exc:
                if self._is_retryable(exc) and attempt < MAX_RETRIES:
                    delay = self._backoff_delay(attempt)
                    logger.warning("Bedrock stream retry %d/%d: %s", attempt + 1, MAX_RETRIES, exc)
                    time.sleep(delay)
                else:
                    yield {"type": "error", "error": str(exc)}
                    return

    def check_availability(self, model_id: str) -> bool:
        """Check model availability via minimal probe."""
        try:
            client = self._get_client()
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
            }
            client.invoke_model(modelId=model_id, body=json.dumps(body))
            return True
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code == "ThrottlingException":
                return True  # Model exists, just rate-limited
            return False
