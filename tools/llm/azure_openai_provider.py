# [TEMPLATE: CUI // SP-CTI]
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Azure OpenAI LLM Provider.

Supports Azure OpenAI Service including Azure Government endpoints
(*.openai.azure.us) for IL4/IL5 workloads. Uses the openai Python SDK
with AzureOpenAI client, api_version, and Azure AD token support.

Follows the D66 provider abstraction pattern (ABC + implementation).
Graceful degradation on missing SDK per D73.

Government endpoints:
- Commercial: https://{resource}.openai.azure.com/
- Azure Government: https://{resource}.openai.azure.us/
- Azure Government (IL5): https://{resource}.openai.azure.us/ (dedicated)

Authentication:
- API Key: AZURE_OPENAI_API_KEY
- Azure AD Token: AZURE_OPENAI_AD_TOKEN or DefaultAzureCredential
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
    messages_to_openai,
    tools_to_openai,
)

logger = logging.getLogger("icdev.llm.azure_openai")

try:
    from openai import AzureOpenAI
    HAS_OPENAI = True
except ImportError:
    AzureOpenAI = None  # type: ignore[assignment, misc]
    HAS_OPENAI = False

try:
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    HAS_AZURE_IDENTITY = True
except ImportError:
    DefaultAzureCredential = None  # type: ignore[assignment, misc]
    get_bearer_token_provider = None  # type: ignore[assignment]
    HAS_AZURE_IDENTITY = False


# Default API version for Azure OpenAI
DEFAULT_API_VERSION = "2024-10-21"

# Government cloud endpoints
AZURE_GOV_SCOPE = "https://cognitiveservices.azure.us/.default"
AZURE_COMMERCIAL_SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI Service provider via the openai SDK AzureOpenAI client.

    Supports both commercial and Azure Government (*.openai.azure.us)
    endpoints. Authentication via API key or Azure AD (Entra ID) tokens.

    Args:
        endpoint: Azure OpenAI resource endpoint URL.
        api_key: API key for authentication (mutually exclusive with use_ad_token).
        api_version: Azure OpenAI API version string.
        use_ad_token: If True, use Azure AD / DefaultAzureCredential.
        ad_token: Pre-fetched Azure AD bearer token (optional).
    """

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        api_version: str = DEFAULT_API_VERSION,
        use_ad_token: bool = False,
        ad_token: str = "",
    ):
        self._endpoint = endpoint or os.environ.get(
            "AZURE_OPENAI_ENDPOINT", ""
        )
        self._api_key = api_key or os.environ.get(
            "AZURE_OPENAI_API_KEY", ""
        )
        self._api_version = api_version or os.environ.get(
            "AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION
        )
        self._use_ad_token = use_ad_token
        self._ad_token = ad_token or os.environ.get(
            "AZURE_OPENAI_AD_TOKEN", ""
        )
        self._client = None

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    @property
    def _is_government(self) -> bool:
        """Check if endpoint is an Azure Government endpoint."""
        return ".azure.us" in self._endpoint.lower()

    def _get_client(self):
        """Lazy-init AzureOpenAI client with appropriate auth."""
        if self._client is not None:
            return self._client

        if not HAS_OPENAI:
            raise ImportError(
                "openai SDK required for Azure OpenAI. "
                "Install: pip install openai"
            )

        if not self._endpoint:
            raise ValueError(
                "Azure OpenAI endpoint required. Set AZURE_OPENAI_ENDPOINT "
                "or pass endpoint= to constructor."
            )

        kwargs: Dict[str, Any] = {
            "azure_endpoint": self._endpoint,
            "api_version": self._api_version,
        }

        if self._use_ad_token or self._ad_token:
            # Azure AD / Entra ID token authentication
            if self._ad_token:
                # Use pre-fetched token
                kwargs["azure_ad_token"] = self._ad_token
            elif HAS_AZURE_IDENTITY:
                # Use DefaultAzureCredential to get token
                scope = (
                    AZURE_GOV_SCOPE if self._is_government
                    else AZURE_COMMERCIAL_SCOPE
                )
                credential = DefaultAzureCredential()
                token_provider = get_bearer_token_provider(
                    credential, scope
                )
                kwargs["azure_ad_token_provider"] = token_provider
            else:
                raise ImportError(
                    "azure-identity SDK required for Azure AD auth. "
                    "Install: pip install azure-identity"
                )
        else:
            # API key authentication
            if not self._api_key:
                raise ValueError(
                    "Azure OpenAI API key required. Set AZURE_OPENAI_API_KEY "
                    "or pass api_key= to constructor."
                )
            kwargs["api_key"] = self._api_key

        self._client = AzureOpenAI(**kwargs)
        return self._client

    def invoke(
        self, request: LLMRequest, model_id: str, model_config: dict
    ) -> LLMResponse:
        """Invoke Azure OpenAI Chat Completions API.

        Note: model_id here is the Azure deployment name, not the
        OpenAI model name.
        """
        client = self._get_client()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 4096)
        effective_max = min(request.max_tokens, max_output)

        # Build messages
        oai_messages: List[Dict[str, Any]] = []
        if request.system_prompt:
            oai_messages.append({
                "role": "system",
                "content": request.system_prompt,
            })
        oai_messages.extend(messages_to_openai(request.messages))

        kwargs: Dict[str, Any] = {
            "model": model_id,  # Azure deployment name
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
        if (
            request.output_schema
            and model_config.get("supports_structured_output", False)
        ):
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
            logger.error("Azure OpenAI API error: %s", exc)
            raise

        # Parse response
        resp = LLMResponse(provider=self.provider_name)
        resp.model_id = model_id
        resp.duration_ms = int((time.time() - start_time) * 1000)
        resp.classification = request.classification

        if hasattr(completion, "usage") and completion.usage:
            resp.input_tokens = getattr(
                completion.usage, "prompt_tokens", 0
            )
            resp.output_tokens = getattr(
                completion.usage, "completion_tokens", 0
            )

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

    def invoke_streaming(
        self, request: LLMRequest, model_id: str, model_config: dict
    ) -> Iterator[dict]:
        """Invoke Azure OpenAI with streaming response."""
        client = self._get_client()
        start_time = time.time()

        max_output = model_config.get("max_output_tokens", 4096)
        effective_max = min(request.max_tokens, max_output)

        oai_messages: List[Dict[str, Any]] = []
        if request.system_prompt:
            oai_messages.append({
                "role": "system",
                "content": request.system_prompt,
            })
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
                        "duration_ms": int(
                            (time.time() - start_time) * 1000
                        ),
                    }
        except Exception as exc:
            logger.error("Azure OpenAI streaming error: %s", exc)
            yield {"type": "error", "error": str(exc)}

    def check_availability(self, model_id: str) -> bool:
        """Check if the Azure OpenAI deployment is reachable."""
        if not HAS_OPENAI:
            return False
        if not self._endpoint:
            return False
        try:
            client = self._get_client()
            # Minimal completion to verify deployment
            client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception:
            return False
