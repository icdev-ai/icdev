# [TEMPLATE: CUI // SP-CTI]
"""Vendor-agnostic LLM provider base classes and data types.

Defines the universal request/response format and abstract interfaces
that all provider implementations must satisfy.
"""

import copy
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class LLMRateLimitError(RuntimeError):
    """Raised when an LLM provider rejects a request due to rate or token limits.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` blocks
    keep working. Callers that want to react specifically to rate-limit
    exhaustion should catch this class and degrade to an alternative provider.

    Attributes:
        provider: Provider name that returned the rate limit.
        model_id: Model ID that was rate-limited.
    """

    def __init__(self, message: str, *, provider: str = "", model_id: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model_id = model_id


# ---------------------------------------------------------------------------
# Prefix-cache capability (cch-cap-01)
# ---------------------------------------------------------------------------
#: The provider does no prefix caching that ICDEV can reach.
PREFIX_CACHE_NONE = "none"
#: The provider caches eligible prefixes by itself. Nothing is requested; the
#: only job is reading the count back (OpenAI, Azure OpenAI).
PREFIX_CACHE_AUTOMATIC = "automatic"
#: The caller marks breakpoints on the wire and the provider honours them
#: (Anthropic, Bedrock).
PREFIX_CACHE_EXPLICIT = "explicit"
#: The provider needs a stored handle with its own TTL, created out of band and
#: referenced by the call (Gemini ``cachedContents``).
PREFIX_CACHE_MANAGED_OBJECT = "managed_object"
#: A locally hosted server reuses the KV cache across calls. Real, and worth
#: declaring — but the payoff is LATENCY, not billing, so cached-token dollars
#: are the wrong unit entirely.
PREFIX_CACHE_LOCAL = "local"

PREFIX_CACHE_SUPPORT_LEVELS = frozenset(
    {
        PREFIX_CACHE_NONE,
        PREFIX_CACHE_AUTOMATIC,
        PREFIX_CACHE_EXPLICIT,
        PREFIX_CACHE_MANAGED_OBJECT,
        PREFIX_CACHE_LOCAL,
    }
)

#: Anthropic's wire value for an ``explicit`` breakpoint. The ONLY place this
#: vendor string is produced from a neutral request.
CACHE_CONTROL_EPHEMERAL = "ephemeral"


@dataclass(frozen=True)
class PrefixCacheCapability:
    """What a provider does when the caller says 'this prefix is worth caching'.

    Declared BY the provider, never inferred by the router. ``none`` and
    ``local`` are first-class answers with a written reason — not gaps to be
    filled in later — because a provider that legitimately has nothing to offer
    and a provider nobody has looked at must not read the same.

    Attributes:
        support: One of :data:`PREFIX_CACHE_SUPPORT_LEVELS`.
        reason: Why this level, in the provider's own terms. Required — an
            undocumented ``none`` is indistinguishable from an unasked question.
        verified: True when the level was checked against vendor behaviour or
            documentation. False means 'not assessed', which is a DIFFERENT
            fact from 'assessed, and the answer is none'.
        reports_cache_tokens: True when the adapter reads the provider's cached
            token counts back into ``LLMResponse.cache_read_input_tokens`` /
            ``cache_creation_input_tokens``. Caching that fires and is never
            recorded is indistinguishable from caching that never fired.
    """

    support: str
    reason: str
    verified: bool = True
    reports_cache_tokens: bool = False

    def __post_init__(self) -> None:
        if self.support not in PREFIX_CACHE_SUPPORT_LEVELS:
            raise ValueError(
                f"Unknown prefix cache support level {self.support!r}. "
                f"Expected one of: {sorted(PREFIX_CACHE_SUPPORT_LEVELS)}"
            )
        if not (self.reason or "").strip():
            raise ValueError("PrefixCacheCapability.reason is required")


#: What an adapter that has never declared anything answers. Deliberately
#: ``verified=False``: the router treats it exactly like ``none``, but a
#: reader (and any future census) can tell 'nobody has looked' apart from
#: 'looked, and there is nothing there'.
UNDECLARED_PREFIX_CACHE = PrefixCacheCapability(
    support=PREFIX_CACHE_NONE,
    reason="This adapter has not declared prefix-cache support; treated as none until it does.",
    verified=False,
)


# ---------------------------------------------------------------------------
# Vendor-agnostic request / response
# ---------------------------------------------------------------------------
@dataclass
class LLMRequest:
    """Vendor-agnostic LLM invocation request."""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    model: str = ""  # logical name from config
    max_tokens: int = 4096
    temperature: float = 1.0
    tools: Optional[List[Dict]] = None  # OpenAI function-calling format
    output_schema: Optional[Dict] = None
    stop_sequences: Optional[List[str]] = None
    effort: str = "medium"  # low, medium, high, max
    skip_injection_scan: bool = False  # True for trusted internal pipeline calls
    # Cache control (cch-cap-01)
    #: The provider-NEUTRAL intent: "this request's prefix is stable and worth
    #: caching". Set by the caller (or by the router from per-canvas /
    #: per-function config); the PROVIDER decides what that means, via its
    #: declared PrefixCacheCapability. This is the field to set.
    cache_prefix: bool = False
    #: Anthropic's wire vocabulary ("ephemeral" | ""), derived from
    #: ``cache_prefix`` by :func:`apply_prefix_cache` for providers that declare
    #: ``explicit`` support, and read only by the Anthropic and Bedrock
    #: adapters. Do NOT set this from a caller or from the router — a vendor
    #: field on a neutral request forces every other vendor to recognise or
    #: ignore a foreign vocabulary. Kept settable for the two adapters' tests
    #: and for callers predating cch-cap-01.
    cache_control: str = ""
    # Chain orchestration fields
    chain_mode: str = ""  # "cot" or "cod" or ""
    chain_config: Dict[str, Any] = field(default_factory=dict)
    # Tracking metadata
    agent_id: str = ""
    project_id: str = ""
    tenant_id: str = ""
    classification: str = "CUI"
    # Run-level correlation id (hgx-obs-01). Set by the agent loop so the
    # gen_ai.invoke span the router opens for this request carries the id of the
    # run that issued it. Carried on the REQUEST rather than in a contextvar
    # because the loop frequently invokes the router on a pool worker, whose
    # context is its own; an explicit field survives that thread hop.
    correlation_id: str = ""


@dataclass
class LLMResponse:
    """Vendor-agnostic LLM invocation response."""

    content: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    structured_output: Optional[Dict] = None
    model_id: str = ""  # actual provider model ID used
    provider: str = ""  # "bedrock", "anthropic", "openai", "ollama"
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0  # 0 for non-Anthropic
    cache_creation_input_tokens: int = 0  # D-CACHE-10: Anthropic prompt cache write cost
    cache_read_input_tokens: int = 0      # D-CACHE-10: Anthropic prompt cache read savings
    duration_ms: int = 0
    stop_reason: str = ""
    cost_usd: float = 0.0                 # USD cost, derived by the router
    #: How cost_usd was arrived at: "priced" | "local_zero" | "unpriced".
    #: NO provider populates cost_usd (all nine adapters leave the default),
    #: so the router derives it from the per-model pricing block and records
    #: the basis alongside - a 0.0 from a local model and a 0.0 from a model
    #: with no price in the table are different facts.
    cost_basis: str = ""
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

    @property
    def prefix_cache_capability(self) -> PrefixCacheCapability:
        """Declare what this provider does with a stable, cacheable prefix.

        Every adapter overrides this. The default is UNDECLARED — reported as
        ``none`` with ``verified=False`` — so an adapter that forgets answers
        honestly instead of inheriting somebody else's vendor behaviour.
        """
        return UNDECLARED_PREFIX_CACHE

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

    def invoke_streaming(self, request: LLMRequest, model_id: str, model_config: dict) -> Iterator[dict]:
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

    def reset_client(self) -> None:
        """Drop any cached SDK/HTTP client so the next call rebuilds it.

        Called by the router when the egress proxy rotates: SDK clients
        (Anthropic/OpenAI) read proxy env only at construction, so a cached
        client must be invalidated to pick up a new proxy. ``requests``-based
        providers (e.g. Ollama) read env per request and need no rebuild.

        The default resets the conventional ``self._client`` attribute if
        present; providers that cache under a different name should override.
        """
        if getattr(self, "_client", None) is not None:
            self._client = None


# ---------------------------------------------------------------------------
# Prefix-cache resolution (cch-cap-01)
# ---------------------------------------------------------------------------
def resolve_prefix_cache_capability(provider: Any) -> PrefixCacheCapability:
    """Return ``provider``'s declared capability, or UNDECLARED.

    Duck-typed on purpose: the router hands over whatever ``_get_provider``
    returned, and test doubles / the CLI bridge are not required to subclass
    :class:`LLMProvider`. A provider that cannot answer is treated as
    undeclared, never as an error on the invocation path.
    """
    cap = getattr(provider, "prefix_cache_capability", None)
    return cap if isinstance(cap, PrefixCacheCapability) else UNDECLARED_PREFIX_CACHE


def wants_prefix_cache(request: LLMRequest) -> bool:
    """True when caching this request's prefix has been asked for.

    Accepts the legacy vendor field as well as the neutral one so callers
    predating cch-cap-01 — and the Anthropic-specific BDD suite — keep working.
    """
    return bool(getattr(request, "cache_prefix", False)) or (
        getattr(request, "cache_control", "") == CACHE_CONTROL_EPHEMERAL
    )


def apply_prefix_cache(provider: Any, request: LLMRequest) -> LLMRequest:
    """Translate the neutral prefix-cache intent into what THIS provider needs.

    Called once the provider is known — which is the whole point, since the
    router picks a chain, not a vendor, and the same request can fall through
    to a second provider with entirely different semantics.

    - ``explicit``: set Anthropic's ``cache_control`` breakpoint marker.
    - ``automatic``: nothing to ask for; the provider caches and reports.
    - ``managed_object``: the handle is created out of band; nothing on the
      request today.
    - ``local`` / ``none``: nothing to set, by design.

    For every level except ``explicit`` a foreign ``cache_control`` value is
    STRIPPED rather than forwarded, so a fallback from Anthropic to (say)
    Gemini cannot carry Anthropic's vocabulary onto Gemini's wire.

    Returns the request unchanged when nothing needs to change, and a shallow
    copy otherwise — never mutates the caller's object, which may be retried
    against a different provider.
    """
    if not wants_prefix_cache(request):
        return request

    cap = resolve_prefix_cache_capability(provider)
    if cap.support == PREFIX_CACHE_EXPLICIT:
        if request.cache_control == CACHE_CONTROL_EPHEMERAL:
            return request
        req = copy.copy(request)
        req.cache_control = CACHE_CONTROL_EPHEMERAL
        return req

    if request.cache_control:
        req = copy.copy(request)
        req.cache_control = ""
        return req
    return request


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


def messages_to_anthropic(messages: List[Dict]):
    """Convert universal message format to Anthropic format.

    Anthropic's Messages API rejects messages with ``role="system"`` — the
    system prompt must be passed as a top-level ``system`` parameter. This
    helper handles the OpenAI-style convention where the system prompt
    travels as the first message: any ``role="system"`` messages are
    extracted (concatenated by newline if multiple) and returned
    separately so the caller can merge them with
    ``LLMRequest.system_prompt``.

    Returns:
        (messages, system_text) tuple.
          * ``messages`` — list with role=user/assistant only, in
            Anthropic content-block form
          * ``system_text`` — concatenated text of any extracted
            system-role messages, or ``None`` if there were none

    Handles image blocks:
    - OpenAI image_url blocks are converted to Anthropic image blocks
    - Anthropic image blocks are passed through unchanged
    """
    result: List[Dict] = []
    system_fragments: List[str] = []

    def _extract_text_from_content(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(p for p in parts if p)
        return ""

    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "user")

        if role == "system":
            text = _extract_text_from_content(content)
            if text:
                system_fragments.append(text)
            continue

        if isinstance(content, str):
            result.append(
                {
                    "role": role,
                    "content": [{"type": "text", "text": content}],
                }
            )
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

    system_text = "\n\n".join(system_fragments) if system_fragments else None
    return result, system_text


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
            result.append(
                {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                }
            )
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
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", tool.get("inputSchema", {})),
                    },
                }
            )
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
        result.append(
            {
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "input": args,
            }
        )
    return result
