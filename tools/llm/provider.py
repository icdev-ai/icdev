# CUI // SP-CTI
"""Vendor-agnostic LLM provider base classes and data types.

Defines the universal request/response format and abstract interfaces
that all provider implementations must satisfy.
"""

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


# ---------------------------------------------------------------------------
# Vendor-agnostic request / response
# ---------------------------------------------------------------------------
@dataclass
class LLMRequest:
    """Vendor-agnostic LLM invocation request."""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    model: str = ""                          # logical name from config
    max_tokens: int = 4096
    temperature: float = 1.0
    tools: Optional[List[Dict]] = None       # OpenAI function-calling format
    output_schema: Optional[Dict] = None
    stop_sequences: Optional[List[str]] = None
    effort: str = "medium"                   # low, medium, high, max
    # Tracking metadata
    agent_id: str = ""
    project_id: str = ""
    classification: str = "CUI"


@dataclass
class LLMResponse:
    """Vendor-agnostic LLM invocation response."""
    content: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    structured_output: Optional[Dict] = None
    model_id: str = ""                       # actual provider model ID used
    provider: str = ""                       # "bedrock", "anthropic", "openai", "ollama"
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0                 # 0 for non-Anthropic
    duration_ms: int = 0
    stop_reason: str = ""
    classification: str = "CUI"


# ---------------------------------------------------------------------------
# Abstract base: LLM Provider
# ---------------------------------------------------------------------------
class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Each provider implementation handles:
    - Message format translation (universal <-> vendor)
    - Tool format translation
    - API invocation with retry
    - Response parsing back to LLMResponse
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. 'bedrock', 'openai')."""

    @abstractmethod
    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Invoke the LLM synchronously.

        Args:
            request: Vendor-agnostic request.
            model_id: Provider-specific model identifier.
            model_config: Model configuration from llm_config.yaml.

        Returns:
            Vendor-agnostic response.
        """

    def invoke_streaming(self, request: LLMRequest, model_id: str,
                         model_config: dict) -> Iterator[dict]:
        """Invoke the LLM with streaming response.

        Default implementation falls back to non-streaming invoke.

        Yields dicts with type key: text, thinking, tool_use_start,
        tool_use_input, message_delta, message_stop, error.
        """
        resp = self.invoke(request, model_id, model_config)
        yield {"type": "text", "text": resp.content}
        yield {
            "type": "message_stop",
            "model_id": resp.model_id,
            "duration_ms": resp.duration_ms,
        }

    @abstractmethod
    def check_availability(self, model_id: str) -> bool:
        """Check if a specific model is available.

        Args:
            model_id: Provider-specific model identifier.

        Returns:
            True if the model can accept requests.
        """


# ---------------------------------------------------------------------------
# Abstract base: Embedding Provider
# ---------------------------------------------------------------------------
class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the embedding dimensionality."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate an embedding vector for a single text.

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding vector.
        """

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        Default implementation calls embed() in a loop.
        Providers with batch APIs should override.

        Args:
            texts: List of input texts.

        Returns:
            List of embedding vectors.
        """
        return [self.embed(t) for t in texts]

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if the embedding model is available."""


# ---------------------------------------------------------------------------
# Message format translators
# ---------------------------------------------------------------------------
def _convert_image_block_to_anthropic(block: dict) -> dict:
    """Convert an OpenAI image_url block to Anthropic image block.

    OpenAI: {"type": "image_url", "image_url": {"url": "data:image/png;base64,DATA"}}
    Anthropic: {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "DATA"}}
    """
    url = block.get("image_url", {}).get("url", "")
    if url.startswith("data:"):
        # Parse data URI: data:image/png;base64,DATA
        header, _, b64_data = url.partition(",")
        media_type = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }
    # Non-data URI (URL reference) — pass through as-is for providers that support it
    return block


def _convert_image_block_to_openai(block: dict) -> dict:
    """Convert an Anthropic image block to OpenAI image_url block.

    Anthropic: {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "DATA"}}
    OpenAI: {"type": "image_url", "image_url": {"url": "data:image/png;base64,DATA"}}
    """
    source = block.get("source", {})
    media_type = source.get("media_type", "image/png")
    b64_data = source.get("data", "")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
    }


def _has_image_blocks(content: list) -> bool:
    """Check if a content block list contains any image blocks."""
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type", "")
            if btype in ("image", "image_url"):
                return True
    return False


def messages_to_anthropic(messages: List[Dict]) -> List[Dict]:
    """Convert universal message format to Anthropic format.

    Anthropic expects: {"role": "user"/"assistant", "content": [{"type": "text", "text": "..."}]}
    Universal may use: {"role": "user", "content": "plain string"} (OpenAI style)

    Handles image blocks:
    - OpenAI image_url blocks are converted to Anthropic image blocks
    - Anthropic image blocks are passed through unchanged
    """
    result = []
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "user")
        if isinstance(content, str):
            result.append({
                "role": role,
                "content": [{"type": "text", "text": content}],
            })
        elif isinstance(content, list):
            # Convert any OpenAI image_url blocks to Anthropic format
            converted_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    converted_blocks.append(_convert_image_block_to_anthropic(block))
                else:
                    converted_blocks.append(block)
            result.append({"role": role, "content": converted_blocks})
        else:
            result.append(msg)
    return result


def messages_to_openai(messages: List[Dict]) -> List[Dict]:
    """Convert universal message format to OpenAI format.

    OpenAI expects: {"role": "user"/"assistant"/"system", "content": "string"}
    Anthropic sends: {"role": "user", "content": [{"type": "text", "text": "..."}]}

    For multimodal messages (containing images), preserves list-of-blocks format
    using OpenAI vision content parts instead of flattening to string.
    """
    result = []
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "user")
        if isinstance(content, list):
            if _has_image_blocks(content):
                # Multimodal: preserve as list of content parts (OpenAI vision format)
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "text":
                            parts.append({"type": "text", "text": block.get("text", "")})
                        elif btype == "image_url":
                            # Already in OpenAI format
                            parts.append(block)
                        elif btype == "image":
                            # Convert Anthropic image to OpenAI image_url
                            parts.append(_convert_image_block_to_openai(block))
                        elif btype == "tool_result":
                            inner = block.get("content", [])
                            for ib in inner:
                                if isinstance(ib, dict) and ib.get("type") == "text":
                                    parts.append({"type": "text", "text": ib.get("text", "")})
                result.append({"role": role, "content": parts})
            else:
                # Text-only: flatten to string (original behavior)
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            inner = block.get("content", [])
                            for ib in inner:
                                if isinstance(ib, dict) and ib.get("type") == "text":
                                    text_parts.append(ib.get("text", ""))
                result.append({"role": role, "content": "\n".join(text_parts)})
        else:
            result.append({"role": role, "content": content})
    return result


def tools_to_anthropic(tools: List[Dict]) -> List[Dict]:
    """Convert OpenAI function-calling tool format to Anthropic tool format.

    OpenAI: {"type": "function", "function": {"name": ..., "parameters": ...}}
    Anthropic: {"name": ..., "description": ..., "input_schema": ...}
    """
    result = []
    for tool in tools:
        if "function" in tool:
            func = tool["function"]
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            })
        elif "name" in tool and "input_schema" in tool:
            # Already in Anthropic format
            result.append(tool)
        else:
            result.append(tool)
    return result


def tools_to_openai(tools: List[Dict]) -> List[Dict]:
    """Convert Anthropic tool format to OpenAI function-calling format.

    Anthropic: {"name": ..., "description": ..., "input_schema": ...}
    OpenAI: {"type": "function", "function": {"name": ..., "parameters": ...}}
    """
    result = []
    for tool in tools:
        if "function" in tool:
            # Already in OpenAI format
            result.append(tool)
        elif "name" in tool:
            result.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("inputSchema", {})),
                },
            })
        else:
            result.append(tool)
    return result


def tool_calls_from_openai(choices_message: dict) -> List[Dict]:
    """Extract tool calls from OpenAI response format into universal format.

    Returns list of {"id": ..., "name": ..., "input": ...}
    """
    result = []
    for tc in choices_message.get("tool_calls", []):
        func = tc.get("function", {})
        args_str = func.get("arguments", "{}")
        try:
            args = json.loads(args_str)
        except (json.JSONDecodeError, ValueError):
            args = {"raw": args_str}
        result.append({
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "input": args,
        })
    return result
