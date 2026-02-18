# CUI // SP-CTI
"""Embedding provider implementations.

Supports OpenAI (including Ollama/vLLM via base_url) and AWS Bedrock
Titan embeddings through a unified EmbeddingProvider interface.
"""

import json
import logging
import os
from typing import List

from tools.llm.provider import EmbeddingProvider

logger = logging.getLogger("icdev.llm.embedding")

try:
    import openai as openai_sdk
    HAS_OPENAI = True
except ImportError:
    openai_sdk = None
    HAS_OPENAI = False

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    HAS_BOTO3 = False


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider.

    Works with OpenAI API, Ollama, vLLM, or any server exposing
    the OpenAI embeddings endpoint via configurable base_url.
    """

    def __init__(self, api_key: str = "", base_url: str = "https://api.openai.com/v1",
                 model_id: str = "text-embedding-3-small", dims: int = 1536):
        self._api_key = api_key
        self._base_url = base_url
        self._model_id = model_id
        self._dims = dims
        self._client = None

    @property
    def provider_name(self) -> str:
        if "localhost" in self._base_url or "127.0.0.1" in self._base_url:
            return "local"
        return "openai"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _get_client(self):
        if self._client is None:
            if not HAS_OPENAI:
                raise ImportError("openai SDK required. Install: pip install openai")
            kwargs = {"base_url": self._base_url}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            else:
                kwargs["api_key"] = "not-needed"
            self._client = openai_sdk.OpenAI(**kwargs)
        return self._client

    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        client = self._get_client()
        response = client.embeddings.create(input=text, model=self._model_id)
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts in one API call."""
        if not texts:
            return []
        client = self._get_client()
        response = client.embeddings.create(input=texts, model=self._model_id)
        # Sort by index to preserve order
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    def check_availability(self) -> bool:
        """Check if embedding endpoint is reachable."""
        if not HAS_OPENAI:
            return False
        try:
            client = self._get_client()
            client.embeddings.create(input="test", model=self._model_id)
            return True
        except Exception:
            return False


class BedrockEmbeddingProvider(EmbeddingProvider):
    """AWS Bedrock embedding provider for Amazon Titan Embeddings."""

    def __init__(self, region: str = None,
                 model_id: str = "amazon.titan-embed-text-v2:0",
                 dims: int = 1024):
        self._region = region or os.environ.get("AWS_DEFAULT_REGION", "us-gov-west-1")
        self._model_id = model_id
        self._dims = dims
        self._client = None

    @property
    def provider_name(self) -> str:
        return "bedrock"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _get_client(self):
        if self._client is None:
            if not HAS_BOTO3:
                raise ImportError("boto3 required. Install: pip install boto3")
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def embed(self, text: str) -> List[float]:
        """Generate embedding via Bedrock Titan."""
        client = self._get_client()
        body = json.dumps({"inputText": text})
        response = client.invoke_model(modelId=self._model_id, body=body)
        result = json.loads(response["body"].read())
        return result.get("embedding", [])

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Titan doesn't have native batch — call embed() in loop."""
        return [self.embed(t) for t in texts]

    def check_availability(self) -> bool:
        """Check if Bedrock Titan embedding is available."""
        if not HAS_BOTO3:
            return False
        try:
            self.embed("test")
            return True
        except Exception:
            return False
