#!/usr/bin/env python3
# CUI // SP-CTI
"""IBM watsonx.ai LLM Provider (D238).

Supports IBM Granite, Llama, and other foundation models via watsonx.ai.
Uses ibm-watsonx-ai SDK with graceful degradation (D73).

Government deployment: watsonx on AWS GovCloud or IBM Cloud for Government (IC4G).
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger("icdev.llm.ibm_watsonx")

# Graceful SDK import (D73)
try:
    from ibm_watsonx_ai import Credentials as WatsonxCredentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames
    HAS_WATSONX = True
except ImportError:
    HAS_WATSONX = False
    WatsonxCredentials = None
    ModelInference = None
    GenTextParamsMetaNames = None


class IBMWatsonxProvider(LLMProvider):
    """IBM watsonx.ai foundation model provider.

    Supports Granite, Llama, and other models hosted on watsonx.ai.
    Configuration via environment variables or constructor args.

    Env vars:
        IBM_CLOUD_API_KEY: IBM Cloud API key
        IBM_WATSONX_PROJECT_ID: watsonx.ai project ID
        IBM_WATSONX_URL: watsonx.ai endpoint URL
    """

    def __init__(self, api_key: str = "", project_id: str = "",
                 url: str = "", model_id: str = ""):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._project_id = project_id or os.environ.get("IBM_WATSONX_PROJECT_ID", "")
        self._url = url or os.environ.get(
            "IBM_WATSONX_URL", "https://us-south.ml.cloud.ibm.com"
        )
        self._default_model_id = model_id or "ibm/granite-3.1-8b-instruct"
        self._credentials = None
        self._models: Dict[str, Any] = {}

    @property
    def provider_name(self) -> str:
        return "ibm_watsonx"

    def _get_credentials(self):
        """Get or create watsonx credentials."""
        if self._credentials is None and HAS_WATSONX and self._api_key:
            self._credentials = WatsonxCredentials(
                api_key=self._api_key,
                url=self._url,
            )
        return self._credentials

    def _get_model(self, model_id: str) -> Optional[Any]:
        """Get or create a model inference client for the given model ID."""
        if model_id in self._models:
            return self._models[model_id]

        creds = self._get_credentials()
        if not creds or not HAS_WATSONX:
            return None

        try:
            model = ModelInference(
                model_id=model_id,
                credentials=creds,
                project_id=self._project_id,
            )
            self._models[model_id] = model
            return model
        except Exception as exc:
            logger.error("Failed to create watsonx model %s: %s", model_id, exc)
            return None

    def invoke(self, request: LLMRequest, model_id: str = "",
               model_config: dict = None) -> LLMResponse:
        """Invoke watsonx.ai model with the given request."""
        if model_config is None:
            model_config = {}

        effective_model_id = model_id or request.model or self._default_model_id

        if not HAS_WATSONX:
            return LLMResponse(
                content="ibm-watsonx-ai SDK not installed",
                model_id=effective_model_id,
                provider=self.provider_name,
                input_tokens=0,
                output_tokens=0,
            )

        model = self._get_model(effective_model_id)
        if not model:
            return LLMResponse(
                content="",
                model_id=effective_model_id,
                provider=self.provider_name,
                input_tokens=0,
                output_tokens=0,
            )

        try:
            # Build generation parameters
            params = {}
            if request.max_tokens:
                params[GenTextParamsMetaNames.MAX_NEW_TOKENS] = request.max_tokens
            if request.temperature is not None:
                params[GenTextParamsMetaNames.TEMPERATURE] = request.temperature

            # Build prompt from messages
            prompt = self._messages_to_prompt(request.messages)

            # Generate response
            response = model.generate_text(
                prompt=prompt,
                params=params,
            )

            # Extract text — generate_text returns string directly
            content = response if isinstance(response, str) else str(response)

            return LLMResponse(
                content=content,
                model_id=effective_model_id,
                provider=self.provider_name,
                input_tokens=0,  # watsonx SDK doesn't always return token counts inline
                output_tokens=0,
            )

        except Exception as exc:
            logger.error("watsonx invoke error: %s", exc)
            raise

    def invoke_streaming(self, request: LLMRequest, model_id: str = "",
                         model_config: dict = None) -> Generator[dict, None, None]:
        """Invoke watsonx.ai model with streaming response."""
        if model_config is None:
            model_config = {}

        effective_model_id = model_id or request.model or self._default_model_id

        if not HAS_WATSONX:
            yield {"type": "error", "error": "ibm-watsonx-ai SDK not installed"}
            return

        model = self._get_model(effective_model_id)
        if not model:
            yield {"type": "error", "error": "Failed to initialize watsonx model"}
            return

        try:
            params = {}
            if request.max_tokens:
                params[GenTextParamsMetaNames.MAX_NEW_TOKENS] = request.max_tokens
            if request.temperature is not None:
                params[GenTextParamsMetaNames.TEMPERATURE] = request.temperature

            prompt = self._messages_to_prompt(request.messages)

            for chunk in model.generate_text_stream(
                prompt=prompt,
                params=params,
            ):
                if chunk:
                    yield {"type": "text", "text": chunk}

            yield {
                "type": "message_stop",
                "model_id": effective_model_id,
            }

        except Exception as exc:
            logger.error("watsonx streaming error: %s", exc)
            yield {"type": "error", "error": str(exc)}

    def check_availability(self, model_id: str = "") -> bool:
        """Check if watsonx.ai is available."""
        if not HAS_WATSONX:
            return False
        if not self._api_key or not self._project_id:
            return False
        try:
            creds = self._get_credentials()
            return creds is not None
        except Exception:
            return False

    @staticmethod
    def _messages_to_prompt(messages: List[Dict]) -> str:
        """Convert chat messages to a prompt string for watsonx models.

        watsonx.ai Granite models use a simple prompt format.
        For chat-style interactions, we build a structured prompt.
        """
        if not messages:
            return ""

        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"<|system|>\n{content}\n")
            elif role == "user":
                parts.append(f"<|user|>\n{content}\n")
            elif role == "assistant":
                parts.append(f"<|assistant|>\n{content}\n")

        # Add final assistant prompt
        parts.append("<|assistant|>\n")
        return "".join(parts)
