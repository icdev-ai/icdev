# [TEMPLATE: CUI // SP-CTI]
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Google Vertex AI LLM Provider.

Supports Google Cloud Vertex AI with Assured Workloads for FedRAMP
High and IL4/IL5 workloads. Uses the google-cloud-aiplatform SDK
for Gemini model access via Vertex AI endpoints.

Follows the D66 provider abstraction pattern (ABC + implementation).
Graceful degradation on missing SDK per D73.

Vertex AI endpoints:
- Commercial: us-central1-aiplatform.googleapis.com
- Assured Workloads: Configured per project/region with compliance controls

Supported models:
- gemini-2.0-flash
- gemini-2.5-pro
- gemini-2.5-flash
"""

import json
import logging
import os
import time
from typing import Any, Dict, Iterator, List

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

logger = logging.getLogger("icdev.llm.vertex_ai")

try:
    from google.cloud import aiplatform
    from vertexai.generative_models import (
        Content,
        GenerationConfig,
        GenerativeModel,
        Part,
    )
    import vertexai
    HAS_VERTEX = True
except ImportError:
    aiplatform = None  # type: ignore[assignment]
    vertexai = None  # type: ignore[assignment]
    HAS_VERTEX = False


def _convert_messages_to_vertex(
    messages: List[Dict[str, Any]],
) -> List[Any]:
    """Convert ICDEV universal messages to Vertex AI Content format.

    Handles plain strings, Anthropic-style content blocks, and
    OpenAI-style content blocks. Maps 'assistant' role to 'model'.
    """
    if not HAS_VERTEX:
        return []

    result = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Vertex AI uses "user" and "model" roles
        vertex_role = "model" if role == "assistant" else "user"

        if isinstance(content, str):
            result.append(
                Content(role=vertex_role, parts=[Part.from_text(content)])
            )
            continue

        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")

                if btype == "text":
                    parts.append(Part.from_text(block.get("text", "")))

                elif btype == "image":
                    # Anthropic format: base64 image
                    source = block.get("source", {})
                    b64_data = source.get("data", "")
                    media_type = source.get("media_type", "image/png")
                    if b64_data:
                        import base64
                        image_bytes = base64.b64decode(b64_data)
                        parts.append(
                            Part.from_data(data=image_bytes, mime_type=media_type)
                        )

                elif btype == "image_url":
                    # OpenAI format: data URI
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:") and "," in url:
                        header, _, b64_data = url.partition(",")
                        media_type = "image/png"
                        if ":" in header and ";" in header:
                            media_type = header.split(":")[1].split(";")[0]
                        import base64
                        image_bytes = base64.b64decode(b64_data)
                        parts.append(
                            Part.from_data(data=image_bytes, mime_type=media_type)
                        )

                elif btype == "tool_result":
                    inner = block.get("content", [])
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "text":
                            parts.append(
                                Part.from_text(ib.get("text", ""))
                            )

            if parts:
                result.append(Content(role=vertex_role, parts=parts))
        else:
            result.append(
                Content(role=vertex_role, parts=[Part.from_text(str(content))])
            )

    return result


class VertexAIProvider(LLMProvider):
    """Google Vertex AI provider using the google-cloud-aiplatform SDK.

    Supports Gemini models via Vertex AI with Assured Workloads
    compliance controls for FedRAMP High and IL4/IL5 workloads.

    Args:
        project: Google Cloud project ID.
        location: GCP region (e.g., 'us-central1', 'us-east4').
        credentials: Optional Google auth credentials object.
        assured_workload: If True, enables Assured Workloads compliance mode.
    """

    def __init__(
        self,
        project: str = "",
        location: str = "",
        credentials: Any = None,
        assured_workload: bool = False,
    ):
        self._project = project or os.environ.get(
            "GOOGLE_CLOUD_PROJECT",
            os.environ.get("GCP_PROJECT", ""),
        )
        self._location = location or os.environ.get(
            "GOOGLE_CLOUD_LOCATION",
            os.environ.get("GCP_REGION", "us-central1"),
        )
        self._credentials = credentials
        self._assured_workload = assured_workload
        self._initialized = False

    @property
    def provider_name(self) -> str:
        return "vertex_ai"

    def _ensure_initialized(self):
        """Initialize Vertex AI SDK (once)."""
        if self._initialized:
            return
        if not HAS_VERTEX:
            raise ImportError(
                "google-cloud-aiplatform SDK required for Vertex AI. "
                "Install: pip install google-cloud-aiplatform"
            )
        if not self._project:
            raise ValueError(
                "Google Cloud project ID required. "
                "Set GOOGLE_CLOUD_PROJECT or pass project= to constructor."
            )

        init_kwargs: Dict[str, Any] = {
            "project": self._project,
            "location": self._location,
        }
        if self._credentials:
            init_kwargs["credentials"] = self._credentials

        vertexai.init(**init_kwargs)
        self._initialized = True

    def invoke(
        self, request: LLMRequest, model_id: str, model_config: dict
    ) -> LLMResponse:
        """Invoke Vertex AI Gemini model synchronously."""
        self._ensure_initialized()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 8192)
        effective_max = min(request.max_tokens, max_output)

        # Build generation config
        gen_config_kwargs: Dict[str, Any] = {
            "max_output_tokens": effective_max,
        }
        if request.temperature is not None:
            gen_config_kwargs["temperature"] = request.temperature
        if request.stop_sequences:
            gen_config_kwargs["stop_sequences"] = request.stop_sequences

        # Structured JSON output
        if (
            request.output_schema
            and model_config.get("supports_structured_output", False)
        ):
            gen_config_kwargs["response_mime_type"] = "application/json"

        gen_config = GenerationConfig(**gen_config_kwargs)

        # Create model
        model_kwargs: Dict[str, Any] = {}
        if request.system_prompt:
            model_kwargs["system_instruction"] = request.system_prompt

        model = GenerativeModel(
            model_name=model_id,
            generation_config=gen_config,
            **model_kwargs,
        )

        # Convert messages
        vertex_messages = _convert_messages_to_vertex(request.messages)

        try:
            response = model.generate_content(vertex_messages)
        except Exception as exc:
            logger.error("Vertex AI API error: %s", exc)
            raise

        # Parse response
        resp = LLMResponse(provider=self.provider_name)
        resp.model_id = model_id
        resp.duration_ms = int((time.time() - start_time) * 1000)
        resp.classification = request.classification

        text_parts: List[str] = []
        tool_calls: List[Dict] = []

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

            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason is not None:
                resp.stop_reason = (
                    str(finish_reason.name).lower()
                    if hasattr(finish_reason, "name")
                    else str(finish_reason)
                )

        resp.content = "\n".join(text_parts)
        resp.tool_calls = tool_calls

        # Token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            resp.input_tokens = (
                getattr(usage, "prompt_token_count", 0) or 0
            )
            resp.output_tokens = (
                getattr(usage, "candidates_token_count", 0) or 0
            )

        # Try parsing structured output
        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def invoke_streaming(
        self, request: LLMRequest, model_id: str, model_config: dict
    ) -> Iterator[dict]:
        """Invoke Vertex AI with streaming response."""
        self._ensure_initialized()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 8192)
        effective_max = min(request.max_tokens, max_output)

        gen_config_kwargs: Dict[str, Any] = {
            "max_output_tokens": effective_max,
        }
        if request.temperature is not None:
            gen_config_kwargs["temperature"] = request.temperature
        if request.stop_sequences:
            gen_config_kwargs["stop_sequences"] = request.stop_sequences

        gen_config = GenerationConfig(**gen_config_kwargs)

        model_kwargs: Dict[str, Any] = {}
        if request.system_prompt:
            model_kwargs["system_instruction"] = request.system_prompt

        model = GenerativeModel(
            model_name=model_id,
            generation_config=gen_config,
            **model_kwargs,
        )

        vertex_messages = _convert_messages_to_vertex(request.messages)

        try:
            response = model.generate_content(
                vertex_messages, stream=True
            )

            for chunk in response:
                if hasattr(chunk, "text") and chunk.text:
                    yield {"type": "text", "text": chunk.text}
                elif hasattr(chunk, "candidates") and chunk.candidates:
                    for candidate in chunk.candidates:
                        if (
                            hasattr(candidate, "content")
                            and candidate.content
                        ):
                            for part in candidate.content.parts:
                                if hasattr(part, "text") and part.text:
                                    yield {
                                        "type": "text",
                                        "text": part.text,
                                    }

            yield {
                "type": "message_stop",
                "model_id": model_id,
                "duration_ms": int((time.time() - start_time) * 1000),
            }

        except Exception as exc:
            logger.error("Vertex AI streaming error: %s", exc)
            yield {"type": "error", "error": str(exc)}

    def check_availability(self, model_id: str) -> bool:
        """Check if the Vertex AI endpoint is reachable."""
        if not HAS_VERTEX:
            return False
        if not self._project:
            return False
        try:
            self._ensure_initialized()
            model = GenerativeModel(model_name=model_id)
            model.generate_content(
                [Content(role="user", parts=[Part.from_text("ping")])],
                generation_config=GenerationConfig(max_output_tokens=1),
            )
            return True
        except Exception:
            return False
