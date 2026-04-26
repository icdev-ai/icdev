# [TEMPLATE: CUI // SP-CTI]
"""Direct Anthropic API LLM Provider.

Uses the anthropic Python SDK for direct API access (not via Bedrock).
Useful when not on AWS or for on-prem with internet access.
"""

import json
import logging
import time
from typing import Any, Dict

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    messages_to_anthropic,
    tools_to_anthropic,
)

logger = logging.getLogger("icdev.llm.anthropic")

try:
    import anthropic as anthropic_sdk

    HAS_ANTHROPIC = True
except ImportError:
    anthropic_sdk = None
    HAS_ANTHROPIC = False


class AnthropicLLMProvider(LLMProvider):
    """Direct Anthropic API provider using the anthropic SDK.

    Supports thinking, tools, structured output — same capabilities
    as Bedrock but via the direct Anthropic API.
    """

    def __init__(self, api_key: str = "", base_url: str = "https://api.anthropic.com"):
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self):
        """Lazy-init anthropic client."""
        if self._client is None:
            if not HAS_ANTHROPIC:
                raise ImportError("anthropic SDK required. Install: pip install anthropic")
            kwargs = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url and self._base_url != "https://api.anthropic.com":
                kwargs["base_url"] = self._base_url
            self._client = anthropic_sdk.Anthropic(**kwargs)
        return self._client

    @staticmethod
    def _effort_to_budget(effort: str, max_tokens: int) -> int:
        """Map effort to thinking budget."""
        ratios = {
            "low": (0.10, 1024),
            "medium": (0.25, 4096),
            "high": (0.60, 10240),
            "max": (1.0, 10240),
        }
        ratio, floor_val = ratios.get(effort, (0.25, 4096))
        return max(int(max_tokens * ratio), floor_val)

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Invoke Anthropic API synchronously."""
        client = self._get_client()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 8192)
        effective_max = min(request.max_tokens, max_output)
        messages, extracted_system = messages_to_anthropic(request.messages)

        kwargs: Dict[str, Any] = {
            "model": model_id,
            "max_tokens": effective_max,
            "messages": messages,
        }

        # Merge any role=system messages extracted from the input list with
        # the top-level system_prompt. Anthropic's Messages API only accepts
        # system text as a top-level parameter.
        system_parts = [s for s in (request.system_prompt, extracted_system) if s]
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        if request.stop_sequences:
            kwargs["stop_sequences"] = request.stop_sequences

        if request.tools:
            kwargs["tools"] = tools_to_anthropic(request.tools)

        # Thinking support — try adaptive, fall back to disabled on API error
        use_thinking = model_config.get("supports_thinking", False)
        if use_thinking:
            effort = request.effort or "medium"
            kwargs["thinking"] = {
                "type": "adaptive",
                "budget_tokens": self._effort_to_budget(effort, effective_max),
            }

        try:
            message = client.messages.create(**kwargs)
        except Exception as exc:
            if use_thinking and "budget_tokens" in str(exc):
                # Model doesn't support adaptive thinking — retry without it
                logger.debug("Adaptive thinking not supported, retrying without: %s", exc)
                kwargs.pop("thinking", None)
                try:
                    message = client.messages.create(**kwargs)
                except Exception as exc2:
                    logger.error("Anthropic API error (no thinking): %s", exc2)
                    raise
            else:
                logger.error("Anthropic API error: %s", exc)
                raise

        # Parse response
        resp = LLMResponse(provider=self.provider_name)
        resp.model_id = model_id
        resp.stop_reason = getattr(message, "stop_reason", "")
        resp.duration_ms = int((time.time() - start_time) * 1000)
        resp.classification = request.classification

        usage = getattr(message, "usage", None)
        if usage:
            resp.input_tokens = getattr(usage, "input_tokens", 0)
            resp.output_tokens = getattr(usage, "output_tokens", 0)

        text_parts = []
        tool_calls = []
        for block in getattr(message, "content", []):
            btype = getattr(block, "type", "")
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}),
                    }
                )
            elif btype == "thinking":
                resp.thinking_tokens += getattr(block, "tokens", 0)

        resp.content = "\n".join(text_parts)
        resp.tool_calls = tool_calls

        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def check_availability(self, model_id: str) -> bool:
        """Check if Anthropic API is reachable."""
        if not HAS_ANTHROPIC:
            return False
        if not self._api_key:
            return False
        try:
            client = self._get_client()
            # Minimal request to verify credentials
            client.messages.create(
                model=model_id,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
