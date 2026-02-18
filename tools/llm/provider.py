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
def messages_to_anthropic(messages: List[Dict]) -> List[Dict]:
    """Convert universal message format to Anthropic format.

    Anthropic expects: {"role": "user"/"assistant", "content": [{"type": "text", "text": "..."}]}
    Universal may use: {"role": "user", "content": "plain string"} (OpenAI style)
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
        else:
            # Already in Anthropic block format
            result.append(msg)
    return result


def messages_to_openai(messages: List[Dict]) -> List[Dict]:
    """Convert universal message format to OpenAI format.

    OpenAI expects: {"role": "user"/"assistant"/"system", "content": "string"}
    Anthropic sends: {"role": "user", "content": [{"type": "text", "text": "..."}]}
    """
    result = []
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "user")
        if isinstance(content, list):
            # Flatten Anthropic content blocks to string
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        # Convert tool_result blocks
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
