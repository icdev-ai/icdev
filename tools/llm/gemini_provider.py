
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Google Gemini LLM Provider.

Uses the google-generativeai Python SDK for Gemini API access.
Supports text generation, vision/multimodal, tool use, structured
output, and streaming.

Follows the D66 provider abstraction pattern (ABC + implementation).
Graceful degradation on missing SDK per D73.
"""

import datetime
import json
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from tools.llm.managed_cache import (
    CACHE_CREATE,
    CACHE_REUSE,
    ManagedCacheDecision,
    ManagedPrefixCache,
    load_managed_cache_config,
)
from tools.llm.provider import (
    PREFIX_CACHE_MANAGED_OBJECT,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    PrefixCacheCapability,
    wants_prefix_cache,
)

logger = get_logger("icdev.llm.gemini")

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
    """Convert ICDEV™ universal messages to Gemini content format.

    Handles three content shapes:
    1. Plain string: {"role": "user", "content": "hello"}
    2. Anthropic list: {"role": "user", "content": [{"type": "text", ...}, {"type": "image", ...}]}
    3. OpenAI list: {"role": "user", "content": [{"type": "text", ...}, {"type": "image_url", ...}]}

    Gemini format:
      {"role": "user", "parts": ["text"]}
      {"role": "user", "parts": [{"text": "desc"}, {"inline_data": {"mime_type": ..., "data": ...}}]}
    """

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
                        parts.append(
                            {
                                "inline_data": {
                                    "mime_type": media_type,
                                    "data": b64_data,
                                }
                            }
                        )

                elif btype == "image_url":
                    # OpenAI format:
                    # {"type": "image_url", "image_url": {"url": "data:image/png;base64,DATA"}}
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:") and "," in url:
                        header, _, b64_data = url.partition(",")
                        media_type = "image/png"
                        if ":" in header and ";" in header:
                            media_type = header.split(":")[1].split(";")[0]
                        parts.append(
                            {
                                "inline_data": {
                                    "mime_type": media_type,
                                    "data": b64_data,
                                }
                            }
                        )

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
    """Convert ICDEV™/OpenAI tool format to Gemini function declarations.

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

    def __init__(self, api_key: str = "", prefix_cache: Optional[ManagedPrefixCache] = None):
        self._api_key = api_key
        self._configured = False
        # cch-prov-02: the managed cachedContents registry. Injectable so the
        # economics gate can be exercised without a key, a network or the SDK.
        self._prefix_cache = prefix_cache

    @property
    def prefix_cache(self) -> ManagedPrefixCache:
        """The cachedContents registry, built from llm_config.yaml on first use."""
        if self._prefix_cache is None:
            self._prefix_cache = ManagedPrefixCache(load_managed_cache_config())
        return self._prefix_cache

    def _ensure_configured(self):
        """Configure the Gemini SDK with the API key (once)."""
        if self._configured:
            return
        if not HAS_GEMINI:
            raise ImportError("google-generativeai SDK required. Install: pip install google-generativeai")
        if self._api_key:
            genai.configure(api_key=self._api_key)
        self._configured = True

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def prefix_cache_capability(self) -> PrefixCacheCapability:
        """Managed object: a stored cachedContents handle with its own TTL."""
        return PrefixCacheCapability(
            support=PREFIX_CACHE_MANAGED_OBJECT,
            reason=(
                "Gemini caches through the cachedContents API: content is uploaded "
                "once, given a TTL, and referenced by handle on later calls — there "
                "is no per-request marker to set, so a cache_control field is "
                "meaningless here. cch-prov-02 implements that lifecycle "
                "(tools/llm/managed_cache.py) and reads "
                "usageMetadata.cachedContentTokenCount back into "
                "cache_read_input_tokens. Default OFF: a stored object is billed "
                "per token per hour, so it is created only for a prefix measured "
                "large enough and seen again inside its TTL."
            ),
            reports_cache_tokens=True,
        )

    # -- managed cachedContents lifecycle (cch-prov-02) -------------------
    @staticmethod
    def _qualified_model(model_id: str) -> str:
        """cachedContents wants the fully-qualified ``models/<id>`` name."""
        return model_id if model_id.startswith("models/") else f"models/{model_id}"

    @staticmethod
    def _tools_fingerprint(tools: Optional[List[Dict]]) -> str:
        """Stable text for the tool set, so a changed tool set is a new prefix."""
        if not tools:
            return ""
        try:
            return json.dumps(tools, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(tools)

    def _decide_cache(self, request: LLMRequest, model_id: str) -> ManagedCacheDecision:
        """Ask the registry what to do with this request's stable prefix.

        The stable prefix is the system instruction plus the tool declarations —
        the parts that repeat verbatim across a surface's calls. Message content
        is deliberately excluded: it is what varies, and a fingerprint that
        included it would never be seen twice.
        """
        return self.prefix_cache.decide(
            self._qualified_model(model_id),
            request.system_prompt or "",
            self._tools_fingerprint(request.tools),
        )

    def _create_cache_object(
        self, decision: ManagedCacheDecision, request: LLMRequest, model_id: str, gemini_tools
    ) -> Tuple[Any, int]:
        """Create a cachedContents object for this prefix. Never raises.

        Returns ``(handle, cached_token_count)``; ``(None, 0)`` when the object
        could not be created, in which case the prefix is suppressed for a
        cooldown so a persistently failing prefix cannot storm the API on every
        single call. A caching failure must never fail the invocation.
        """
        ttl = self.prefix_cache.config.ttl_seconds
        try:
            kwargs: Dict[str, Any] = {
                "model": self._qualified_model(model_id),
                "ttl": datetime.timedelta(seconds=ttl),
            }
            if request.system_prompt:
                kwargs["system_instruction"] = request.system_prompt
            if gemini_tools:
                kwargs["tools"] = gemini_tools
            handle = genai.caching.CachedContent.create(**kwargs)
        except Exception as exc:
            # Includes the vendor's own minimum-token refusal: our estimate is a
            # chars/4 heuristic, so it can sit above the local floor and below
            # the real one. Falling through uncached is the correct answer.
            logger.info("Gemini cachedContents create failed, continuing uncached: %s", exc)
            self.prefix_cache.note_failure(decision.fingerprint)
            return None, 0

        tokens = 0
        usage = getattr(handle, "usage_metadata", None)
        if usage is not None:
            tokens = getattr(usage, "total_token_count", 0) or 0

        if not self.prefix_cache.record_object(decision.fingerprint, handle, tokens):
            # Another thread registered an object for this prefix first. Release
            # ours rather than leave it renting unreferenced for its whole TTL.
            self._delete_cache_object(handle)
            return None, 0

        logger.debug(
            "Gemini cachedContents created (%s tokens, ttl=%ss): %s",
            tokens, ttl, decision.reason,
        )
        return handle, tokens

    @staticmethod
    def _delete_cache_object(handle: Any) -> None:
        """Best-effort early release. The TTL expires it regardless."""
        try:
            handle.delete()
        except Exception as exc:
            logger.debug("Gemini cachedContents delete failed (TTL will expire it): %s", exc)

    def _model_from_cache(self, handle: Any, gen_config: Dict[str, Any]):
        """Build a GenerativeModel bound to a cache object, or None on failure.

        System instruction and tools come from the cached object; passing them
        again is an error, which is why the uncached path owns ``model_kwargs``
        and this one does not.
        """
        try:
            return genai.GenerativeModel.from_cached_content(
                cached_content=handle, generation_config=gen_config
            )
        except Exception as exc:
            logger.info("Gemini from_cached_content failed, continuing uncached: %s", exc)
            return None

    def _resolve_model(
        self,
        request: LLMRequest,
        model_id: str,
        gen_config: Dict[str, Any],
        model_kwargs: Dict[str, Any],
        gemini_tools,
    ) -> Tuple[Any, ManagedCacheDecision, int]:
        """Build the model to call, through a cache object when that pays.

        Returns ``(model, decision, created_tokens)``. ``decision`` is None when
        the caller never asserted ``cache_prefix``; ``created_tokens`` is
        non-zero only on the call that created the object.
        """
        decision = None
        if wants_prefix_cache(request):
            decision = self._decide_cache(request, model_id)
            if decision.action == CACHE_REUSE:
                model = self._model_from_cache(decision.handle, gen_config)
                if model is not None:
                    return model, decision, 0
                # The handle no longer works; forget it and fall through.
                self.prefix_cache.drop(decision.fingerprint)
            elif decision.action == CACHE_CREATE:
                handle, created = self._create_cache_object(
                    decision, request, model_id, gemini_tools
                )
                if handle is not None:
                    model = self._model_from_cache(handle, gen_config)
                    if model is not None:
                        return model, decision, created
                    self.prefix_cache.drop(decision.fingerprint)
                    self._delete_cache_object(handle)

        model = genai.GenerativeModel(
            model_name=model_id, generation_config=gen_config, **model_kwargs
        )
        return model, decision, 0

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
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

        # Create model instance — through a cachedContents object when the
        # prefix is large enough and repeated enough to pay for its storage.
        model, cache_decision, created_tokens = self._resolve_model(
            request, model_id, gen_config, model_kwargs, gemini_tools
        )
        used_cache_object = cache_decision is not None and cache_decision.uses_object

        # Convert messages
        gemini_messages = _convert_messages_to_gemini(request.messages)

        try:
            response = model.generate_content(gemini_messages)
        except Exception as exc:
            if not used_cache_object:
                logger.error("Gemini API error: %s", exc)
                raise
            # The vendor can expire or reject a cache object between our TTL
            # bookkeeping and the call. Retry once without it: a caching
            # optimisation must never be the reason a request fails.
            logger.info("Gemini call with a cache object failed, retrying uncached: %s", exc)
            self.prefix_cache.drop(cache_decision.fingerprint)
            created_tokens = 0
            used_cache_object = False
            model = genai.GenerativeModel(
                model_name=model_id, generation_config=gen_config, **model_kwargs
            )
            try:
                response = model.generate_content(gemini_messages)
            except Exception as retry_exc:
                logger.error("Gemini API error: %s", retry_exc)
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
                        tool_calls.append(
                            {
                                "id": f"call_{len(tool_calls)}",
                                "name": fc.name,
                                "input": dict(fc.args) if fc.args else {},
                            }
                        )

            # Stop reason
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason is not None:
                resp.stop_reason = (
                    str(finish_reason.name).lower() if hasattr(finish_reason, "name") else str(finish_reason)
                )

        resp.content = "\n".join(text_parts)
        resp.tool_calls = tool_calls

        # Token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            resp.input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            resp.output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            resp.thinking_tokens = getattr(usage, "thoughts_token_count", 0) or 0
            # Cache-token parity (cch-prov-01) + the storage event (cch-prov-02).
            #
            # Gemini sends cachedContentTokenCount in usageMetadata for
            # implicit caching and for the explicit cachedContents API alike.
            # Read unconditionally, not only when we created an object: a
            # count we did not ask for is still a count worth recording.
            #
            # BOTH spellings, because the SDK snake_cases the field while a
            # raw REST payload does not.
            #
            # NOTE for anyone summing across providers — prompt_token_count
            # INCLUDES the cached tokens (OpenAI's convention, the opposite of
            # Anthropic's), so input_tokens + cache_read_input_tokens
            # double-counts them here.
            resp.cache_read_input_tokens = (
                getattr(usage, "cached_content_token_count", 0)
                or getattr(usage, "cachedContentTokenCount", 0)
                or 0
            )

            # The storage event: how many tokens this call put into a stored object.
            # Reported on the creating call ONLY, so a write is never mistaken for a
            # read.
            #
            # cch-prov-01 left this field at 0 and said so deliberately — correct
            # while nothing created objects, since Gemini reports no creation-token
            # count and inventing one would fabricate a write cost. cch-prov-02
            # creates the object, so the count is now a fact we hold rather than one
            # we would have to invent. Unlike Anthropic's 1.25x write premium,
            # Gemini bills storage per token-hour: same token count, different price
            # attached to it.
            if created_tokens:
                resp.cache_creation_input_tokens = created_tokens

        # Try parsing structured output
        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def invoke_streaming(self, request: LLMRequest, model_id: str, model_config: dict) -> Iterator[dict]:
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

        gemini_tools = None
        if request.tools and model_config.get("supports_tools", False):
            declarations = _convert_tools_to_gemini(request.tools)
            if declarations:
                gemini_tools = [genai_types.Tool(function_declarations=declarations)]
                model_kwargs["tools"] = gemini_tools

        # Same cachedContents lifecycle as the non-streaming path — a surface
        # that streams has the same stable prefix and the same economics. The
        # stream yields no LLMResponse, so cached tokens are not reported here;
        # the object itself is shared with invoke(), which does report them.
        model, _decision, _created = self._resolve_model(
            request, model_id, gen_config, model_kwargs, gemini_tools
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
