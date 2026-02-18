# CUI // SP-CTI
"""Native Ollama LLM Provider using the Ollama REST API directly.

Uses requests.post() against the Ollama native API endpoints:
- /api/chat    — chat completions (text + vision)
- /api/tags    — model listing / availability check

This provider handles the Anthropic-style multimodal message format
(used internally by ICDEV's LLMRequest) and converts it to Ollama's
native image format: {"role": "user", "content": "text", "images": ["base64"]}.
"""

import json
import logging
import time
from typing import Any, Dict, Iterator, List

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

logger = logging.getLogger("icdev.llm.ollama")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore[assignment]
    HAS_REQUESTS = False


# ---------------------------------------------------------------------------
# Message format conversion: universal -> Ollama native
# ---------------------------------------------------------------------------

def _convert_messages_to_ollama(messages: List[Dict[str, Any]],
                                system_prompt: str = "") -> List[Dict[str, Any]]:
    """Convert ICDEV universal messages to Ollama native chat format.

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

                elif btype == "tool_result":
                    # Flatten tool_result content blocks to text
                    inner = block.get("content", [])
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "text":
                            text_parts.append(ib.get("text", ""))

            ollama_msg: Dict[str, Any] = {
                "role": role,
                "content": "\n".join(text_parts),
            }
            if images:
                ollama_msg["images"] = images
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

    Does NOT use the OpenAI-compatible endpoint (/v1/chat/completions).
    Instead uses the native /api/chat and /api/tags endpoints directly,
    which provides access to Ollama-specific features like native
    multimodal image handling.
    """

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url.rstrip("/")
        self._timeout = 120  # seconds

    @property
    def provider_name(self) -> str:
        return "ollama"

    def invoke(self, request: LLMRequest, model_id: str,
               model_config: dict) -> LLMResponse:
        """Invoke Ollama via native /api/chat (non-streaming)."""
        if not HAS_REQUESTS:
            raise ImportError("requests library required. Install: pip install requests")

        start_time = time.time()

        # Build Ollama messages
        ollama_messages = _convert_messages_to_ollama(
            request.messages, request.system_prompt
        )

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
        effective_max = min(request.max_tokens, max_output)
        options["num_predict"] = effective_max

        if request.stop_sequences:
            options["stop"] = request.stop_sequences

        if options:
            payload["options"] = options

        # Structured output via Ollama's format parameter
        if request.output_schema and model_config.get("supports_structured_output", False):
            payload["format"] = "json"

        try:
            resp_http = requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout,
            )
            resp_http.raise_for_status()
        except requests.ConnectionError:
            logger.error("Ollama connection refused at %s", self._base_url)
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is Ollama running? Start with: ollama serve"
            )
        except requests.Timeout:
            logger.error("Ollama request timed out after %ds", self._timeout)
            raise TimeoutError(
                f"Ollama request timed out after {self._timeout}s"
            )
        except requests.HTTPError as exc:
            logger.error("Ollama HTTP error: %s %s", resp_http.status_code, resp_http.text)
            raise RuntimeError(
                f"Ollama returned HTTP {resp_http.status_code}: {resp_http.text}"
            ) from exc

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

    def invoke_streaming(self, request: LLMRequest, model_id: str,
                         model_config: dict) -> Iterator[dict]:
        """Invoke Ollama with streaming via native /api/chat."""
        if not HAS_REQUESTS:
            yield {"type": "error", "error": "requests library required"}
            return

        start_time = time.time()

        ollama_messages = _convert_messages_to_ollama(
            request.messages, request.system_prompt
        )

        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": ollama_messages,
            "stream": True,
        }

        options: Dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature

        max_output = model_config.get("max_output_tokens", 4096)
        effective_max = min(request.max_tokens, max_output)
        options["num_predict"] = effective_max

        if request.stop_sequences:
            options["stop"] = request.stop_sequences

        if options:
            payload["options"] = options

        if request.output_schema and model_config.get("supports_structured_output", False):
            payload["format"] = "json"

        try:
            resp_http = requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                stream=True,
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
            resp = requests.get(
                f"{self._base_url}/api/tags",
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
