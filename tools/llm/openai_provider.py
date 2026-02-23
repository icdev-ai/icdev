# [TEMPLATE: CUI // SP-CTI]
"""OpenAI-compatible LLM Provider.

Supports any server that implements the OpenAI Chat Completions API:
- OpenAI (api.openai.com)
- Ollama (localhost:11434)
- vLLM (localhost:8000)
- Azure OpenAI
- Any OpenAI-API-compatible server

Uses the openai Python SDK with configurable base_url.
"""

import json
import logging
import time
from typing import Any, Dict, Iterator

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    messages_to_openai,
    tools_to_openai,
)

logger = logging.getLogger("icdev.llm.openai_compat")

try:
    import openai as openai_sdk
    HAS_OPENAI = True
except ImportError:
    openai_sdk = None
    HAS_OPENAI = False


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider via the openai SDK.

    One implementation handles OpenAI, Ollama, vLLM, and Azure OpenAI
    by configuring the base_url parameter.
    """

    def __init__(self, api_key: str = "", base_url: str = "https://api.openai.com/v1",
                 provider_label: str = "openai"):
        self._api_key = api_key
        self._base_url = base_url
        self._provider_label = provider_label
        self._client = None

    @property
    def provider_name(self) -> str:
        return self._provider_label

    def _get_client(self):
        """Lazy-init OpenAI client with custom base_url."""
        if self._client is None:
            if not HAS_OPENAI:
                raise ImportError(
                    "openai SDK required. Install: pip install openai"
                )
            kwargs = {"base_url": self._base_url}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            else:
                # Some servers (Ollama) don't need a key
                kwargs["api_key"] = "not-needed"
            self._client = openai_sdk.OpenAI(**kwargs)
        return self._client

    def invoke(self, request: LLMRequest, model_id: str,
               model_config: dict) -> LLMResponse:
        """Invoke via OpenAI Chat Completions API."""
        client = self._get_client()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 4096)
        effective_max = min(request.max_tokens, max_output)

        # Build messages
        oai_messages = []
        if request.system_prompt:
            oai_messages.append({"role": "system", "content": request.system_prompt})
        oai_messages.extend(messages_to_openai(request.messages))

        kwargs: Dict[str, Any] = {
            "model": model_id,
            "messages": oai_messages,
            "max_tokens": effective_max,
        }

        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        if request.stop_sequences:
            kwargs["stop"] = request.stop_sequences

        # Tools
        if request.tools and model_config.get("supports_tools", False):
            kwargs["tools"] = tools_to_openai(request.tools)
            kwargs["tool_choice"] = "auto"

        # Structured output
        if request.output_schema and model_config.get("supports_structured_output", False):
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.output_schema.get("name", "response"),
                    "schema": request.output_schema,
                },
            }

        try:
            completion = client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error("OpenAI-compat API error (%s): %s", self._provider_label, exc)
            raise

        # Parse response
        resp = LLMResponse(provider=self._provider_label)
        resp.model_id = model_id
        resp.duration_ms = int((time.time() - start_time) * 1000)
        resp.classification = request.classification

        if hasattr(completion, "usage") and completion.usage:
            resp.input_tokens = getattr(completion.usage, "prompt_tokens", 0)
            resp.output_tokens = getattr(completion.usage, "completion_tokens", 0)

        choice = completion.choices[0] if completion.choices else None
        if choice:
            resp.stop_reason = getattr(choice, "finish_reason", "")
            message = getattr(choice, "message", None)
            if message:
                resp.content = getattr(message, "content", "") or ""
                # Tool calls
                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tc in message.tool_calls:
                        func = getattr(tc, "function", None)
                        if func:
                            args_str = getattr(func, "arguments", "{}")
                            try:
                                args = json.loads(args_str)
                            except (json.JSONDecodeError, ValueError):
                                args = {"raw": args_str}
                            resp.tool_calls.append({
                                "id": getattr(tc, "id", ""),
                                "name": getattr(func, "name", ""),
                                "input": args,
                            })

        # Try parsing structured output
        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def invoke_streaming(self, request: LLMRequest, model_id: str,
                         model_config: dict) -> Iterator[dict]:
        """Invoke with streaming via OpenAI API."""
        client = self._get_client()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 4096)
        effective_max = min(request.max_tokens, max_output)

        oai_messages = []
        if request.system_prompt:
            oai_messages.append({"role": "system", "content": request.system_prompt})
        oai_messages.extend(messages_to_openai(request.messages))

        kwargs: Dict[str, Any] = {
            "model": model_id,
            "messages": oai_messages,
            "max_tokens": effective_max,
            "stream": True,
        }

        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        if request.stop_sequences:
            kwargs["stop"] = request.stop_sequences

        try:
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    yield {"type": "text", "text": delta.content}
                finish = chunk.choices[0].finish_reason
                if finish:
                    yield {
                        "type": "message_stop",
                        "model_id": model_id,
                        "duration_ms": int((time.time() - start_time) * 1000),
                    }
        except Exception as exc:
            yield {"type": "error", "error": str(exc)}

    def check_availability(self, model_id: str) -> bool:
        """Check if the OpenAI-compatible server is reachable."""
        if not HAS_OPENAI:
            return False
        try:
            client = self._get_client()
            # Try listing models first (lightweight)
            try:
                client.models.list()
                return True
            except Exception:
                pass
            # Fall back to minimal completion
            client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception:
            return False
