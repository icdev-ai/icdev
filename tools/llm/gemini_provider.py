# CUI // SP-CTI
"""Google Gemini LLM Provider.

Uses the google-generativeai Python SDK for Gemini API access.
Supports text generation, vision/multimodal, tool use, structured
output, and streaming.

Follows the D66 provider abstraction pattern (ABC + implementation).
Graceful degradation on missing SDK per D73.
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

logger = logging.getLogger("icdev.llm.gemini")

try:
    import google.generativeai as genai
    from google.generativeai import types as genai_types
    HAS_GEMINI = True
except ImportError:
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]
    HAS_GEMINI = False


# ---------------------------------------------------------------------------
# Message format conversion: universal -> Gemini
# ---------------------------------------------------------------------------

def _convert_messages_to_gemini(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert ICDEV universal messages to Gemini content format.

    Handles three content shapes:
    1. Plain string: {"role": "user", "content": "hello"}
    2. Anthropic list: {"role": "user", "content": [{"type": "text", ...}, {"type": "image", ...}]}
    3. OpenAI list: {"role": "user", "content": [{"type": "text", ...}, {"type": "image_url", ...}]}

    Gemini format:
      {"role": "user", "parts": ["text"]}
      {"role": "user", "parts": [{"text": "desc"}, {"inline_data": {"mime_type": ..., "data": ...}}]}
    """
    import base64

    result: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Gemini uses "user" and "model" roles (not "assistant")
        gemini_role = "model" if role == "assistant" else "user"

        if isinstance(content, str):
            result.append({"role": gemini_role, "parts": [content]})
            continue

        if isinstance(content, list):
            parts: List[Any] = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")

                if btype == "text":
                    parts.append(block.get("text", ""))

                elif btype == "image":
                    # Anthropic format:
                    # {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
                    source = block.get("source", {})
                    b64_data = source.get("data", "")
                    media_type = source.get("media_type", "image/png")
                    if b64_data:
                        parts.append({
                            "inline_data": {
                                "mime_type": media_type,
                                "data": b64_data,
                            }
                        })

                elif btype == "image_url":
                    # OpenAI format:
                    # {"type": "image_url", "image_url": {"url": "data:image/png;base64,DATA"}}
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:") and "," in url:
                        header, _, b64_data = url.partition(",")
                        media_type = "image/png"
                        if ":" in header and ";" in header:
                            media_type = header.split(":")[1].split(";")[0]
                        parts.append({
                            "inline_data": {
                                "mime_type": media_type,
                                "data": b64_data,
                            }
                        })

                elif btype == "tool_result":
                    # Flatten tool_result content to text
                    inner = block.get("content", [])
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "text":
                            parts.append(ib.get("text", ""))

            if parts:
                result.append({"role": gemini_role, "parts": parts})
        else:
            result.append({"role": gemini_role, "parts": [str(content)]})

    return result


def _convert_tools_to_gemini(tools: List[Dict]) -> List[Any]:
    """Convert ICDEV/OpenAI tool format to Gemini function declarations.

    Input (OpenAI): {"type": "function", "function": {"name": ..., "parameters": ...}}
    Input (Anthropic): {"name": ..., "description": ..., "input_schema": ...}
    Output (Gemini): genai_types.FunctionDeclaration(name=..., parameters=...)
    """
    if not HAS_GEMINI:
        return []

    declarations = []
    for tool in tools:
        name = ""
        description = ""
        parameters = {}

        if "function" in tool:
            func = tool["function"]
            name = func.get("name", "")
            description = func.get("description", "")
            parameters = func.get("parameters", {})
        elif "name" in tool:
            name = tool.get("name", "")
            description = tool.get("description", "")
            parameters = tool.get("input_schema", tool.get("inputSchema", {}))

        if name:
            declarations.append(
                genai_types.FunctionDeclaration(
                    name=name,
                    description=description,
                    parameters=parameters if parameters else None,
                )
            )

    return declarations


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    """Google Gemini API provider using the google-generativeai SDK.

    Supports text generation, multimodal (vision), tool use,
    structured JSON output, and streaming.
    """

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._configured = False

    def _ensure_configured(self):
        """Configure the Gemini SDK with the API key (once)."""
        if self._configured:
            return
        if not HAS_GEMINI:
            raise ImportError(
                "google-generativeai SDK required. "
                "Install: pip install google-generativeai"
            )
        if self._api_key:
            genai.configure(api_key=self._api_key)
        self._configured = True

    @property
    def provider_name(self) -> str:
        return "gemini"

    def invoke(self, request: LLMRequest, model_id: str,
               model_config: dict) -> LLMResponse:
        """Invoke Gemini API synchronously."""
        self._ensure_configured()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 8192)
        effective_max = min(request.max_tokens, max_output)

        # Build generation config
        gen_config: Dict[str, Any] = {
            "max_output_tokens": effective_max,
        }
        if request.temperature is not None:
            gen_config["temperature"] = request.temperature
        if request.stop_sequences:
            gen_config["stop_sequences"] = request.stop_sequences

        # Structured JSON output
        if request.output_schema and model_config.get("supports_structured_output", False):
            gen_config["response_mime_type"] = "application/json"

        # Thinking / reasoning (Gemini 2.5 Pro supports this)
        if model_config.get("supports_thinking", False):
            effort = request.effort or "medium"
            if effort in ("high", "max"):
                gen_config["thinking_config"] = {"thinking_budget": effective_max}

        # Build model kwargs
        model_kwargs: Dict[str, Any] = {}
        if request.system_prompt:
            model_kwargs["system_instruction"] = request.system_prompt

        # Tool support
        gemini_tools = None
        if request.tools and model_config.get("supports_tools", False):
            declarations = _convert_tools_to_gemini(request.tools)
            if declarations:
                gemini_tools = [genai_types.Tool(function_declarations=declarations)]
                model_kwargs["tools"] = gemini_tools

        # Create model instance
        model = genai.GenerativeModel(
            model_name=model_id,
            generation_config=gen_config,
            **model_kwargs,
        )

        # Convert messages
        gemini_messages = _convert_messages_to_gemini(request.messages)

        try:
            response = model.generate_content(gemini_messages)
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            raise

        # Parse response
        resp = LLMResponse(provider=self.provider_name)
        resp.model_id = model_id
        resp.duration_ms = int((time.time() - start_time) * 1000)
        resp.classification = request.classification

        # Extract content
        text_parts = []
        tool_calls = []

        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content:
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
                    elif hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        tool_calls.append({
                            "id": f"call_{len(tool_calls)}",
                            "name": fc.name,
                            "input": dict(fc.args) if fc.args else {},
                        })

            # Stop reason
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason is not None:
                resp.stop_reason = str(finish_reason.name).lower() if hasattr(finish_reason, "name") else str(finish_reason)

        resp.content = "\n".join(text_parts)
        resp.tool_calls = tool_calls

        # Token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            resp.input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            resp.output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            resp.thinking_tokens = getattr(usage, "thoughts_token_count", 0) or 0

        # Try parsing structured output
        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def invoke_streaming(self, request: LLMRequest, model_id: str,
                         model_config: dict) -> Iterator[dict]:
        """Invoke Gemini with streaming response."""
        self._ensure_configured()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 8192)
        effective_max = min(request.max_tokens, max_output)

        gen_config: Dict[str, Any] = {
            "max_output_tokens": effective_max,
        }
        if request.temperature is not None:
            gen_config["temperature"] = request.temperature
        if request.stop_sequences:
            gen_config["stop_sequences"] = request.stop_sequences
        if request.output_schema and model_config.get("supports_structured_output", False):
            gen_config["response_mime_type"] = "application/json"

        model_kwargs: Dict[str, Any] = {}
        if request.system_prompt:
            model_kwargs["system_instruction"] = request.system_prompt

        if request.tools and model_config.get("supports_tools", False):
            declarations = _convert_tools_to_gemini(request.tools)
            if declarations:
                model_kwargs["tools"] = [genai_types.Tool(function_declarations=declarations)]

        model = genai.GenerativeModel(
            model_name=model_id,
            generation_config=gen_config,
            **model_kwargs,
        )

        gemini_messages = _convert_messages_to_gemini(request.messages)

        try:
            response = model.generate_content(gemini_messages, stream=True)

            for chunk in response:
                if hasattr(chunk, "text") and chunk.text:
                    yield {"type": "text", "text": chunk.text}
                elif hasattr(chunk, "candidates") and chunk.candidates:
                    for candidate in chunk.candidates:
                        if hasattr(candidate, "content") and candidate.content:
                            for part in candidate.content.parts:
                                if hasattr(part, "text") and part.text:
                                    yield {"type": "text", "text": part.text}

            yield {
                "type": "message_stop",
                "model_id": model_id,
                "duration_ms": int((time.time() - start_time) * 1000),
            }

        except Exception as exc:
            logger.error("Gemini streaming error: %s", exc)
            yield {"type": "error", "error": str(exc)}

    def check_availability(self, model_id: str) -> bool:
        """Check if Gemini API is reachable and the model exists."""
        if not HAS_GEMINI:
            return False
        if not self._api_key:
            return False
        try:
            self._ensure_configured()
            # List models to verify API key and connectivity
            models = genai.list_models()
            model_names = []
            for m in models:
                model_names.append(getattr(m, "name", ""))
            # Gemini model names are like "models/gemini-2.0-flash"
            target = f"models/{model_id}" if not model_id.startswith("models/") else model_id
            target_base = model_id.split("-preview")[0] if "-preview" in model_id else model_id
            for name in model_names:
                if target in name or target_base in name or model_id in name:
                    return True
            # If we got a response at all, the API is working — model might
            # be a preview not yet in list_models
            return len(model_names) > 0
        except Exception:
            return False
