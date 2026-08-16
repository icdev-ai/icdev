
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Direct Anthropic API LLM Provider.

Uses the anthropic Python SDK for direct API access (not via Bedrock).
Useful when not on AWS or for on-prem with internet access.
"""

import json
import time
from typing import Any, Dict

from tools.llm.provider import (
    PREFIX_CACHE_EXPLICIT,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    PrefixCacheCapability,
    messages_to_anthropic,
    tools_to_anthropic,
)

logger = get_logger("icdev.llm.anthropic")

try:
    import anthropic as anthropic_sdk

    HAS_ANTHROPIC = True
except ImportError:
    anthropic_sdk = None
    HAS_ANTHROPIC = False


_CACHE_BREAKPOINT_MARKER = "<!-- cache_breakpoint -->"


class AnthropicLLMProvider(LLMProvider):
    """Direct Anthropic API provider using the anthropic SDK.

    Supports thinking, tools, structured output — same capabilities
    as Bedrock but via the direct Anthropic API.

    D-CACHE-RAG-1: Multi-breakpoint support.
    System prompts containing '<!-- cache_breakpoint -->' markers are split
    into separate text blocks, each with cache_control={'type':'ephemeral'}.
    Up to MAX_CACHE_BREAKPOINTS breakpoints are honoured (Anthropic limit).
    """

    # Anthropic hard limit: max 4 cache_control blocks per request (D-CACHE-RAG-2)
    MAX_CACHE_BREAKPOINTS: int = 4

    def __init__(self, api_key: str = "", base_url: str = "https://api.anthropic.com"):
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def prefix_cache_capability(self) -> PrefixCacheCapability:
        """Explicit: breakpoints are requested on the wire, max 4 per request."""
        return PrefixCacheCapability(
            support=PREFIX_CACHE_EXPLICIT,
            reason=(
                "Anthropic caches only what the request marks: up to "
                f"{self.MAX_CACHE_BREAKPOINTS} cache_control={{'type':'ephemeral'}} "
                "breakpoints over a >=1024-token prefix, 5-minute default TTL. "
                "The provider decides where they land (system blocks split on the "
                "cache_breakpoint marker, plus the last user message)."
            ),
            reports_cache_tokens=True,
        )

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
            system_text = "\n\n".join(system_parts)
            # D-CACHE-6 / D-CACHE-RAG-1: Multi-breakpoint Anthropic prompt caching.
            # Split system_text on '<!-- cache_breakpoint -->' markers to create
            # separate blocks with cache_control. Cap at MAX_CACHE_BREAKPOINTS (4).
            if request.cache_control == "ephemeral" and HAS_ANTHROPIC:
                if _CACHE_BREAKPOINT_MARKER in system_text:
                    raw_segments = system_text.split(_CACHE_BREAKPOINT_MARKER)
                    # Cap total blocks to MAX_CACHE_BREAKPOINTS; merge extras into last
                    max_blocks = self.MAX_CACHE_BREAKPOINTS
                    if len(raw_segments) > max_blocks:
                        # Keep first (max_blocks-1) segments, merge remainder into last
                        segments = raw_segments[: max_blocks - 1] + [
                            _CACHE_BREAKPOINT_MARKER.join(raw_segments[max_blocks - 1 :])
                        ]
                    else:
                        segments = raw_segments
                    # Build block list: all but last get cache_control
                    system_blocks = []
                    for i, seg in enumerate(segments):
                        block: Dict[str, Any] = {"type": "text", "text": seg}
                        if i < len(segments) - 1:
                            block["cache_control"] = {"type": "ephemeral"}
                        system_blocks.append(block)
                    kwargs["system"] = system_blocks
                else:
                    # Single block with cache_control (existing D-CACHE-6 behaviour)
                    kwargs["system"] = [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
            else:
                kwargs["system"] = system_text

        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        if request.stop_sequences:
            kwargs["stop_sequences"] = request.stop_sequences

        if request.tools:
            kwargs["tools"] = tools_to_anthropic(request.tools)

        # D-CACHE-7: Mark last user message with cache_control for Anthropic
        if request.cache_control == "ephemeral" and messages:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", [])
                    if isinstance(content, list) and content:
                        last_block = content[-1]
                        if isinstance(last_block, dict) and last_block.get("type") == "text":
                            last_block["cache_control"] = {"type": "ephemeral"}
                    break

        # Thinking support — skip if route config sets disable_thinking: true
        route_config = getattr(request, "_route_config", {}) or {}
        use_thinking = (
            model_config.get("supports_thinking", False)
            and not route_config.get("disable_thinking", False)
        )
        if use_thinking:
            effort = request.effort or "medium"
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._effort_to_budget(effort, effective_max),
            }

        try:
            message = client.messages.create(**kwargs)
        except Exception as exc:
            exc_str = str(exc).lower()
            is_rate_limit = (
                "rate limit" in exc_str
                or "ratelimit" in exc_str
                or "429" in exc_str
                or "too many requests" in exc_str
                or "token limit" in exc_str
                or "quota exceeded" in exc_str
                or "usage limit" in exc_str
                or "billing" in exc_str
                or "capacity" in exc_str
                or "please try again" in exc_str
                or "exceeded" in exc_str
            )
            if is_rate_limit:
                from tools.llm.provider import LLMRateLimitError

                raise LLMRateLimitError(
                    f"Anthropic rate limit: {exc}",
                    provider="anthropic",
                    model_id=model_id,
                ) from exc
            if use_thinking:
                # Model or endpoint doesn't support extended thinking — retry without it
                logger.warning("Extended thinking not supported, retrying without: %s", exc)
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
            resp.cache_creation_input_tokens = getattr(usage, "cache_creation_input_tokens", 0)
            resp.cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", 0)

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
            # Minimal request — hard 8-second timeout to prevent dashboard hangs
            client.messages.create(
                model=model_id,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
                timeout=8.0,
            )
            return True
        except Exception:
            return False
