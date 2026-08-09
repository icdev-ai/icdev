
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Native Ollama LLM Provider using the Ollama REST API directly.

Uses requests.post() against the Ollama native API endpoints:
- /api/chat    — chat completions (text + vision)
- /api/tags    — model listing / availability check

This provider handles the Anthropic-style multimodal message format
(used internally by ICDEV™'s LLMRequest) and converts it to Ollama's
native image format: {"role": "user", "content": "text", "images": ["base64"]}.
"""

import json
import time
from typing import Any, Dict, Iterator, List

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    tools_to_openai,
)

logger = get_logger("icdev.llm.ollama")

try:
    import requests

    from tools.http.client import request as _http_request

    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore[assignment]
    _http_request = None  # type: ignore[assignment]
    HAS_REQUESTS = False


# ---------------------------------------------------------------------------
# Message format conversion: universal -> Ollama native
# ---------------------------------------------------------------------------


def _convert_messages_to_ollama(messages: List[Dict[str, Any]], system_prompt: str = "") -> List[Dict[str, Any]]:
    """Convert ICDEV™ universal messages to Ollama native chat format.

    Handles three content shapes:
    1. Plain string:  {"role": "user", "content": "hello"}
    2. Anthropic list: {"role": "user", "content": [{"type": "text", ...}, {"type": "image", ...}]}
    3. OpenAI list:    {"role": "user", "content": [{"type": "text", ...}, {"type": "image_url", ...}]}

    Ollama native format:
      {"role": "user", "content": "text", "images": ["base64data"]}
    """
    result: List[Dict[str, Any]] = []

    # Ollama supports system role natively
    if system_prompt:
        result.append({"role": "system", "content": system_prompt})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if isinstance(content, list):
            text_parts: List[str] = []
            images: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            tool_results: List[tuple] = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")

                if btype == "text":
                    text_parts.append(block.get("text", ""))

                elif btype == "image":
                    # Anthropic format:
                    # {"type": "image", "source": {"type": "base64", "data": "..."}}
                    source = block.get("source", {})
                    b64 = source.get("data", "")
                    if b64:
                        images.append(b64)

                elif btype == "image_url":
                    # OpenAI format:
                    # {"type": "image_url", "image_url": {"url": "data:image/png;base64,DATA"}}
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:") and "," in url:
                        b64 = url.split(",", 1)[1]
                        images.append(b64)

                elif btype == "tool_use":
                    # Assistant tool call — Ollama expects tool_calls on the
                    # assistant message as [{"function": {"name", "arguments"}}].
                    tool_calls.append(
                        {
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": block.get("input") or {},
                            }
                        }
                    )

                elif btype == "tool_result":
                    # Tool result — Ollama uses a dedicated role:"tool" message
                    # (flattening it into a user message drops the linkage and
                    # makes the model re-invoke the tool every turn).
                    inner = block.get("content", [])
                    rtext = "\n".join(
                        ib.get("text", "")
                        for ib in inner
                        if isinstance(ib, dict) and ib.get("type") == "text"
                    )
                    tool_results.append((block.get("name", "") or "", rtext))

            # Emit each tool result as its own role:"tool" message.
            for tname, rtext in tool_results:
                tm: Dict[str, Any] = {"role": "tool", "content": rtext}
                if tname:
                    tm["name"] = tname
                result.append(tm)

            # Emit the carrying message when it has text/images/tool_calls. A
            # message that only carried tool_result blocks is fully represented
            # by the role:"tool" messages above — don't also emit an empty one.
            if text_parts or images or tool_calls:
                ollama_msg: Dict[str, Any] = {
                    "role": role,
                    "content": "\n".join(text_parts),
                }
                if images:
                    ollama_msg["images"] = images
                if tool_calls:
                    ollama_msg["tool_calls"] = tool_calls
                result.append(ollama_msg)
        else:
            # Fallback: pass through
            result.append({"role": role, "content": str(content)})

    return result


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------


class OllamaProvider(LLMProvider):
    """Native Ollama provider using the Ollama REST API.

    Works with both local Ollama (http://localhost:11434, no auth) and
    cloud Ollama endpoints (e.g. https://ollama.com) that require a Bearer
    token.  Pass api_key or set the env var referenced by api_key_env in
    the provider config; leave blank for unauthenticated local installs.
    """

    def __init__(self, base_url: str = "http://localhost:11434", api_key: str = ""):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = 300  # seconds (increased for GPU contention with image gen)

    def _auth_headers(self) -> dict:
        """Return Authorization header when an API key is configured."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    @property
    def provider_name(self) -> str:
        return "ollama"

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Invoke Ollama via native /api/chat (non-streaming)."""
        if not HAS_REQUESTS:
            raise ImportError("requests library required. Install: pip install requests")

        start_time = time.time()

        # Build Ollama messages
        ollama_messages = _convert_messages_to_ollama(request.messages, request.system_prompt)

        # Build request payload
        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": ollama_messages,
            "stream": False,
        }

        # Ollama options (temperature, num_predict, stop)
        options: Dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature

        max_output = model_config.get("max_output_tokens", 4096)
        # Never clamp below a sane minimum; some models return empty content
        # when num_predict is small relative to the reasoning budget.
        effective_max = max(min(request.max_tokens, max_output), 256)
        options["num_predict"] = effective_max

        if request.stop_sequences:
            options["stop"] = request.stop_sequences

        if options:
            payload["options"] = options

        # Tools — Ollama's /api/chat takes the OpenAI function shape on `tools`.
        # This half of the wiring was missing: the RESPONSE side below already
        # normalises `message.tool_calls`, but nothing ever advertised the tools,
        # so the model could not emit a call and every agent loop over Ollama
        # returned prose on turn 1 with `done=True` and zero tool calls. Because
        # args/llm_config.yaml declares `supports_tools: true` for these models,
        # the loop never raised AgentLoopUnsupported either — it silently
        # degraded into a chat completion that looked like a completed agent run.
        # Measured by hgx-exec-04: the owned executor could not edit a single file.
        if request.tools and model_config.get("supports_tools", False):
            payload["tools"] = tools_to_openai(request.tools)

        # Disable thinking mode when explicitly configured, when the model claims it
        # does not support thinking, or for qwen3 models. Some Ollama endpoints emit
        # reasoning in a "thinking" field that consumes the num_predict budget and
        # leaves content empty unless thinking is disabled.
        if (
            model_config.get("disable_thinking", False)
            or not model_config.get("supports_thinking", True)
            or "qwen3" in model_id.lower()
        ):
            payload["think"] = False

        # Structured output via Ollama's format parameter
        if request.output_schema and model_config.get("supports_structured_output", False):
            payload["format"] = "json"

        try:
            resp_http = _http_request(
                "POST",
                f"{self._base_url}/api/chat",
                json=payload,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
            resp_http.raise_for_status()
        except requests.ConnectionError:
            logger.error("Ollama connection refused at %s", self._base_url)
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. Is Ollama running? Start with: ollama serve"
            )
        except requests.Timeout:
            logger.error("Ollama request timed out after %ds", self._timeout)
            raise TimeoutError(f"Ollama request timed out after {self._timeout}s")
        except requests.HTTPError as exc:
            logger.error("Ollama HTTP error: %s %s", resp_http.status_code, resp_http.text)
            raise RuntimeError(f"Ollama returned HTTP {resp_http.status_code}: {resp_http.text}") from exc

        data = resp_http.json()

        # Parse response
        response = LLMResponse(provider="ollama")
        response.model_id = model_id
        response.duration_ms = int((time.time() - start_time) * 1000)
        response.classification = request.classification

        # Extract content from Ollama response
        message = data.get("message", {})
        response.content = message.get("content", "")

        # Stop reason
        done_reason = data.get("done_reason", "")
        if done_reason:
            response.stop_reason = done_reason
        elif data.get("done", False):
            response.stop_reason = "stop"

        # Tool calls — Ollama's /api/chat returns these on `message.tool_calls`
        # as [{"id": ..., "function": {"name": ..., "arguments": <dict|str>}}].
        # Normalise to the cross-provider {id, name, input} shape used by the
        # OpenAI/Anthropic providers and the agent-loop primitive. Without this
        # every Ollama model reports tool_calls=[] and cannot drive a tool-use
        # loop even when the model emitted calls.
        raw_tool_calls = message.get("tool_calls") or []
        for tc in raw_tool_calls:
            func = tc.get("function") or {}
            name = func.get("name") or tc.get("name") or ""
            if not name:
                continue
            args = func.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {"raw": args}
            if not isinstance(args, dict):
                args = {} if args is None else {"raw": args}
            response.tool_calls.append(
                {
                    "id": tc.get("id") or f"call_{name}_{len(response.tool_calls)}",
                    "name": name,
                    "input": args,
                }
            )
        if response.tool_calls and not done_reason:
            response.stop_reason = "tool_use"

        # Token usage (Ollama provides these at top level)
        response.input_tokens = data.get("prompt_eval_count", 0) or 0
        response.output_tokens = data.get("eval_count", 0) or 0

        # Try parsing structured output
        if response.content.strip().startswith(("{", "[")):
            try:
                response.structured_output = json.loads(response.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return response

    def invoke_streaming(self, request: LLMRequest, model_id: str, model_config: dict) -> Iterator[dict]:
        """Invoke Ollama with streaming via native /api/chat."""
        if not HAS_REQUESTS:
            yield {"type": "error", "error": "requests library required"}
            return

        start_time = time.time()

        ollama_messages = _convert_messages_to_ollama(request.messages, request.system_prompt)

        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": ollama_messages,
            "stream": True,
        }

        options: Dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature

        max_output = model_config.get("max_output_tokens", 4096)
        # Never clamp below a sane minimum; some models return empty content
        # when num_predict is small relative to the reasoning budget.
        effective_max = max(min(request.max_tokens, max_output), 256)
        options["num_predict"] = effective_max

        if request.stop_sequences:
            options["stop"] = request.stop_sequences

        if options:
            payload["options"] = options

        # Tools — same omission as invoke(); see the comment there.
        if request.tools and model_config.get("supports_tools", False):
            payload["tools"] = tools_to_openai(request.tools)

        # Disable thinking mode when explicitly configured, when the model claims it
        # does not support thinking, or for qwen3 models (see invoke() comment).
        if (
            model_config.get("disable_thinking", False)
            or not model_config.get("supports_thinking", True)
            or "qwen3" in model_id.lower()
        ):
            payload["think"] = False

        if request.output_schema and model_config.get("supports_structured_output", False):
            payload["format"] = "json"

        try:
            resp_http = _http_request(
                "POST",
                f"{self._base_url}/api/chat",
                json=payload,
                stream=True,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
            resp_http.raise_for_status()

            for line in resp_http.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                # Each streaming chunk has {"message": {"content": "..."}, "done": bool}
                message = chunk.get("message", {})
                content = message.get("content", "")
                if content:
                    yield {"type": "text", "text": content}

                if chunk.get("done", False):
                    yield {
                        "type": "message_stop",
                        "model_id": model_id,
                        "duration_ms": int((time.time() - start_time) * 1000),
                    }

        except requests.ConnectionError:
            yield {
                "type": "error",
                "error": f"Cannot connect to Ollama at {self._base_url}",
            }
        except requests.Timeout:
            yield {
                "type": "error",
                "error": f"Ollama streaming timed out after {self._timeout}s",
            }
        except Exception as exc:
            yield {"type": "error", "error": str(exc)}

    def check_availability(self, model_id: str) -> bool:
        """Check if Ollama is running and the specified model is available.

        GETs /api/tags and checks if model_id appears in the model list.
        Ollama model names may include tags (e.g. 'llama3:latest'), so we
        match both the full name and the base name without tag.
        """
        if not HAS_REQUESTS:
            return False
        try:
            resp = _http_request(
                "GET",
                f"{self._base_url}/api/tags",
                headers=self._auth_headers(),
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()

            models = data.get("models", [])
            # Normalize the requested model_id for flexible matching
            requested = model_id.lower().strip()
            requested_base = requested.split(":")[0]

            for model in models:
                name = model.get("name", "").lower().strip()
                name_base = name.split(":")[0]
                # Match full name (e.g. "llama3:latest") or base name (e.g. "llama3")
                if name == requested or name_base == requested_base:
                    return True

            return False

        except Exception:
            return False
