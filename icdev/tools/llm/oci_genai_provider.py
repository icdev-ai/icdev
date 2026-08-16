
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Oracle Cloud Infrastructure (OCI) Generative AI LLM Provider.

Supports OCI Generative AI Service for Cohere and Meta Llama models
hosted on Oracle Cloud dedicated AI clusters. Uses the oci Python SDK.

Follows the D66 provider abstraction pattern (ABC + implementation).
Graceful degradation on missing SDK per D73.

Supported models:
- Cohere Command R / Command R+
- Meta Llama 3.1 (70B, 405B)

OCI GenAI endpoints:
- Commercial: https://inference.generativeai.{region}.oci.oraclecloud.com
- Government: OCI Government Cloud regions
"""

import json
import os
import time
from typing import Any, Dict, Iterator, List

from tools.llm.provider import (
    PREFIX_CACHE_AUTOMATIC,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    PrefixCacheCapability,
)

logger = get_logger("icdev.llm.oci_genai")

try:
    import oci
    from oci.generative_ai_inference import GenerativeAiInferenceClient
    from oci.generative_ai_inference.models import (
        ChatDetails,
        CohereChatRequest,
        GenericChatRequest,
        OnDemandServingMode,
        DedicatedServingMode,
    )

    HAS_OCI = True
except ImportError:
    oci = None  # type: ignore[assignment]
    HAS_OCI = False


def _build_cohere_messages(
    messages: List[Dict[str, Any]],
    system_prompt: str = "",
) -> Dict[str, Any]:
    """Build Cohere chat request parameters from universal messages.

    Cohere chat format uses:
    - preamble_override: system prompt
    - message: latest user message
    - chat_history: previous turns as list of {role, message}
    """
    chat_history = []
    latest_user_message = ""

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Flatten list content to string
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            content = "\n".join(text_parts)

        cohere_role = "USER" if role == "user" else "CHATBOT"
        chat_history.append(
            {
                "role": cohere_role,
                "message": content,
            }
        )

    # Extract the last user message
    if chat_history and chat_history[-1]["role"] == "USER":
        latest_user_message = chat_history[-1]["message"]
        chat_history = chat_history[:-1]

    result: Dict[str, Any] = {
        "message": latest_user_message,
    }
    if chat_history:
        result["chat_history"] = chat_history
    if system_prompt:
        result["preamble_override"] = system_prompt

    return result


def _build_generic_messages(
    messages: List[Dict[str, Any]],
    system_prompt: str = "",
) -> List[Dict[str, str]]:
    """Build generic (Llama-style) chat messages from universal format.

    Generic chat uses OpenAI-compatible message format:
    [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    """
    result: List[Dict[str, str]] = []
    if system_prompt:
        result.append({"role": "system", "content": system_prompt})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Flatten list content to string
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            content = "\n".join(text_parts)

        result.append({"role": role, "content": content})

    return result


def _read_usage_into(chat_response: Any, chat_result: Any, resp: LLMResponse) -> None:
    """Copy OCI token counts onto the neutral response, cached tokens included.

    Usage lives on the chat response — ``CohereChatResponse.usage`` and
    ``GenericChatResponse.usage`` are both ``Usage`` (API version 20231130).
    It is NOT on ``ChatResult``, whose only properties are ``model_id``,
    ``model_version`` and ``chat_response``; the adapter read a
    ``ChatResult.model_usage`` that the SDK has never had, so every OCI call
    reported zero tokens. The old path is kept as a fallback rather than
    deleted, because an SDK the tests cannot import is not one to be
    confident about, and ``getattr`` on an absent attribute costs nothing.

    ``Usage.prompt_tokens`` follows the OpenAI convention and INCLUDES the
    cached tokens (Anthropic's excludes them), so do not add
    ``cache_read_input_tokens`` to ``input_tokens`` when costing an OCI call.
    OCI reports reads only — there is no cache-creation counter — so
    ``cache_creation_input_tokens`` stays 0 for this provider.
    """
    usage = getattr(chat_response, "usage", None)
    if usage is None:
        usage = getattr(chat_result, "model_usage", None)
    if usage is None:
        return

    resp.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    resp.output_tokens = getattr(usage, "completion_tokens", 0) or 0

    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        resp.cache_read_input_tokens = getattr(details, "cached_tokens", 0) or 0


class OCIGenAIProvider(LLMProvider):
    """Oracle OCI Generative AI provider using the oci Python SDK.

    Supports Cohere Command R/R+ and Meta Llama models via OCI
    GenAI Service. Uses either on-demand or dedicated serving modes.

    Args:
        compartment_id: OCI compartment OCID.
        region: OCI region identifier (e.g., 'us-chicago-1').
        config_profile: OCI config profile name (default: 'DEFAULT').
        serving_mode: 'on_demand' or 'dedicated'.
        dedicated_endpoint_id: OCID for dedicated AI cluster (if serving_mode='dedicated').
    """

    def __init__(
        self,
        compartment_id: str = "",
        region: str = "",
        config_profile: str = "DEFAULT",
        serving_mode: str = "on_demand",
        dedicated_endpoint_id: str = "",
    ):
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_ID", "")
        self._region = region or os.environ.get("OCI_REGION", "")
        self._config_profile = config_profile
        self._serving_mode = serving_mode
        self._dedicated_endpoint_id = dedicated_endpoint_id or os.environ.get("OCI_GENAI_DEDICATED_ENDPOINT", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "oci_genai"

    @property
    def prefix_cache_capability(self) -> PrefixCacheCapability:
        """Automatic — CHECKED 2026-08-16, and the earlier 'none' was wrong.

        OCI's usage object carries a cached-token counter and its request
        models carry no cache field: nothing to ask for, something to read
        back. That is the same shape as OpenAI and Azure, not a gap.
        """
        return PrefixCacheCapability(
            support=PREFIX_CACHE_AUTOMATIC,
            reason=(
                "Verified 2026-08-16 (cch-prov-04). OCI Generative AI inference "
                "(API version 20231130) returns "
                "`Usage.prompt_tokens_details.cached_tokens` — 'Cached tokens present "
                "in the prompt', wire name `cachedTokens` — on BOTH response shapes "
                "this adapter handles (CohereChatResponse.usage and "
                "GenericChatResponse.usage are both `Usage`). No request-side cache "
                "model exists anywhere in oci.generative_ai_inference.models, so there "
                "is nothing to ask for: automatic, exactly like OpenAI. This adapter "
                "reads the counter into cache_read_input_tokens. HONEST LIMIT: the SDK "
                "proves the counter exists, not that the service populates it non-zero "
                "for any given model — Oracle publishes no prompt-caching page for the "
                "chat API. That is now MEASURABLE (a persistent zero on real OCI "
                "traffic is an answer) rather than assumed. See "
                "docs/research/cch-prov-04-watsonx-oci-cache-verification.md."
            ),
            verified=True,
            reports_cache_tokens=True,
        )

    def _get_client(self):
        """Lazy-init OCI GenerativeAiInferenceClient."""
        if self._client is not None:
            return self._client

        if not HAS_OCI:
            raise ImportError("oci SDK required for OCI GenAI. Install: pip install oci")

        if not self._compartment_id:
            raise ValueError(
                "OCI compartment ID required. Set OCI_COMPARTMENT_ID or pass compartment_id= to constructor."
            )

        try:
            config = oci.config.from_file(profile_name=self._config_profile)
            if self._region:
                config["region"] = self._region
        except Exception:
            # Fall back to instance principal or resource principal
            logger.info("OCI config file not found, using instance principal")
            config = {}

        service_endpoint = None
        if self._region:
            service_endpoint = f"https://inference.generativeai.{self._region}.oci.oraclecloud.com"

        client_kwargs: Dict[str, Any] = {}
        if service_endpoint:
            client_kwargs["service_endpoint"] = service_endpoint

        if config:
            self._client = GenerativeAiInferenceClient(config, **client_kwargs)
        else:
            # Instance principal auth
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            self._client = GenerativeAiInferenceClient({}, signer=signer, **client_kwargs)

        return self._client

    def _is_cohere_model(self, model_id: str) -> bool:
        """Check if the model ID refers to a Cohere model."""
        return "cohere" in model_id.lower() or "command" in model_id.lower()

    def _get_serving_mode(self, model_id: str):
        """Build the appropriate serving mode object."""
        if not HAS_OCI:
            raise ImportError("oci SDK required")

        if self._serving_mode == "dedicated" and self._dedicated_endpoint_id:
            return DedicatedServingMode(endpoint_id=self._dedicated_endpoint_id)
        return OnDemandServingMode(model_id=model_id)

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Invoke OCI GenAI Chat API."""
        client = self._get_client()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 4096)
        effective_max = min(request.max_tokens, max_output)

        serving_mode = self._get_serving_mode(model_id)

        # Build request based on model type
        if self._is_cohere_model(model_id):
            cohere_params = _build_cohere_messages(request.messages, request.system_prompt)
            chat_request = CohereChatRequest(
                message=cohere_params["message"],
                chat_history=cohere_params.get("chat_history"),
                preamble_override=cohere_params.get("preamble_override"),
                max_tokens=effective_max,
                temperature=request.temperature,
                is_stream=False,
            )
        else:
            # Generic (Llama) format
            generic_messages = _build_generic_messages(request.messages, request.system_prompt)
            chat_request = GenericChatRequest(
                messages=generic_messages,
                max_tokens=effective_max,
                temperature=request.temperature,
                is_stream=False,
            )

        chat_details = ChatDetails(
            compartment_id=self._compartment_id,
            serving_mode=serving_mode,
            chat_request=chat_request,
        )

        try:
            response = client.chat(chat_details)
        except Exception as exc:
            logger.error("OCI GenAI API error: %s", exc)
            raise

        # Parse response
        resp = LLMResponse(provider=self.provider_name)
        resp.model_id = model_id
        resp.duration_ms = int((time.time() - start_time) * 1000)
        resp.classification = request.classification

        chat_response = response.data.chat_response

        if self._is_cohere_model(model_id):
            # Cohere response
            resp.content = getattr(chat_response, "text", "") or ""
            resp.stop_reason = getattr(chat_response, "finish_reason", "") or ""

            # Tool calls from Cohere
            tool_calls = getattr(chat_response, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    resp.tool_calls.append(
                        {
                            "id": getattr(tc, "id", f"call_{len(resp.tool_calls)}"),
                            "name": getattr(tc, "name", ""),
                            "input": getattr(tc, "parameters", {}),
                        }
                    )
        else:
            # Generic (Llama) response
            choices = getattr(chat_response, "choices", [])
            if choices:
                choice = choices[0]
                message = getattr(choice, "message", None)
                if message:
                    content = getattr(message, "content", [])
                    if isinstance(content, str):
                        resp.content = content
                    elif isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if hasattr(block, "text"):
                                text_parts.append(block.text)
                        resp.content = "\n".join(text_parts)
                resp.stop_reason = getattr(choice, "finish_reason", "") or ""

        # Token usage (if available)
        _read_usage_into(chat_response, response.data, resp)

        # Try parsing structured output
        if resp.content.strip().startswith(("{", "[")):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    def invoke_streaming(self, request: LLMRequest, model_id: str, model_config: dict) -> Iterator[dict]:
        """Invoke OCI GenAI with streaming response.

        Note: OCI GenAI streaming uses server-sent events.
        Falls back to non-streaming if streaming is not supported.
        """
        # OCI GenAI streaming requires different request setup
        try:
            client = self._get_client()
            start_time = time.time()

            max_output = model_config.get("max_output_tokens", 4096)
            effective_max = min(request.max_tokens, max_output)

            serving_mode = self._get_serving_mode(model_id)

            if self._is_cohere_model(model_id):
                cohere_params = _build_cohere_messages(request.messages, request.system_prompt)
                chat_request = CohereChatRequest(
                    message=cohere_params["message"],
                    chat_history=cohere_params.get("chat_history"),
                    preamble_override=cohere_params.get("preamble_override"),
                    max_tokens=effective_max,
                    temperature=request.temperature,
                    is_stream=True,
                )
            else:
                generic_messages = _build_generic_messages(request.messages, request.system_prompt)
                chat_request = GenericChatRequest(
                    messages=generic_messages,
                    max_tokens=effective_max,
                    temperature=request.temperature,
                    is_stream=True,
                )

            chat_details = ChatDetails(
                compartment_id=self._compartment_id,
                serving_mode=serving_mode,
                chat_request=chat_request,
            )

            response = client.chat(chat_details)

            # Process streaming events
            for event in response.data.events():
                data = json.loads(event.data)
                if self._is_cohere_model(model_id):
                    text = data.get("text", "")
                else:
                    choices = data.get("choices", [])
                    text = ""
                    if choices:
                        delta = choices[0].get("delta", {})
                        text = delta.get("content", "")

                if text:
                    yield {"type": "text", "text": text}

            yield {
                "type": "message_stop",
                "model_id": model_id,
                "duration_ms": int((time.time() - start_time) * 1000),
            }

        except Exception as exc:
            logger.warning(
                "OCI GenAI streaming failed, falling back to non-streaming: %s",
                exc,
            )
            # Fall back to non-streaming
            resp = self.invoke(request, model_id, model_config)
            yield {"type": "text", "text": resp.content}
            yield {
                "type": "message_stop",
                "model_id": resp.model_id,
                "duration_ms": resp.duration_ms,
            }

    def check_availability(self, model_id: str) -> bool:
        """Check if the OCI GenAI service is reachable."""
        if not HAS_OCI:
            return False
        if not self._compartment_id:
            return False
        try:
            client = self._get_client()
            # Attempt a minimal chat to verify connectivity
            serving_mode = self._get_serving_mode(model_id)

            if self._is_cohere_model(model_id):
                chat_request = CohereChatRequest(
                    message="ping",
                    max_tokens=1,
                    is_stream=False,
                )
            else:
                chat_request = GenericChatRequest(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    is_stream=False,
                )

            chat_details = ChatDetails(
                compartment_id=self._compartment_id,
                serving_mode=serving_mode,
                chat_request=chat_request,
            )
            client.chat(chat_details)
            return True
        except Exception:
            return False
