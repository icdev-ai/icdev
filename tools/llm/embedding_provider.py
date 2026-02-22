# CUI // SP-CTI
"""Embedding provider implementations.

Supports OpenAI (including Ollama/vLLM via base_url), AWS Bedrock Titan,
and Google Gemini embeddings through a unified EmbeddingProvider interface.
"""

import json
import logging
import os
from typing import List, Optional

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

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    genai = None  # type: ignore[assignment]
    HAS_GEMINI = False


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


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google Gemini embedding provider using google-generativeai SDK.

    Uses the Gemini embedding models (e.g. text-embedding-004).
    Follows D66 provider pattern and D73 graceful degradation.
    """

    def __init__(self, api_key: str = "",
                 model_id: str = "models/text-embedding-004",
                 dims: int = 768):
        self._api_key = api_key
        self._model_id = model_id if model_id.startswith("models/") else f"models/{model_id}"
        self._dims = dims
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

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed(self, text: str) -> List[float]:
        """Generate embedding via Gemini API."""
        self._ensure_configured()
        result = genai.embed_content(
            model=self._model_id,
            content=text,
        )
        return result["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        Gemini embed_content supports batch input via list of strings.
        """
        if not texts:
            return []
        self._ensure_configured()
        result = genai.embed_content(
            model=self._model_id,
            content=texts,
        )
        return result["embedding"]

    def check_availability(self) -> bool:
        """Check if Gemini embedding API is reachable."""
        if not HAS_GEMINI:
            return False
        if not self._api_key:
            return False
        try:
            self._ensure_configured()
            self.embed("test")
            return True
        except Exception:
            return False


# ============================================================
# Azure OpenAI Embeddings (D238)
# ============================================================
try:
    from openai import AzureOpenAI as _AzureEmbedClient
    _HAS_AZURE_EMBED = True
except ImportError:
    _HAS_AZURE_EMBED = False


class AzureEmbeddingProvider(EmbeddingProvider):
    """Azure OpenAI embedding provider."""

    def __init__(self, api_key: str = "", endpoint: str = "",
                 api_version: str = "2024-02-01",
                 deployment: str = "text-embedding-ada-002"):
        self._api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self._endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self._api_version = api_version
        self._deployment = deployment
        self._client = None
        self._dims = 1536

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _get_client(self):
        if self._client is None and _HAS_AZURE_EMBED and self._api_key:
            self._client = _AzureEmbedClient(
                api_key=self._api_key,
                azure_endpoint=self._endpoint,
                api_version=self._api_version,
            )
        return self._client

    def embed(self, text: str) -> Optional[List[float]]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.embeddings.create(input=text, model=self._deployment)
            return resp.data[0].embedding
        except Exception:
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        client = self._get_client()
        if not client:
            return [None] * len(texts)
        try:
            resp = client.embeddings.create(input=texts, model=self._deployment)
            return [d.embedding for d in resp.data]
        except Exception:
            return [None] * len(texts)

    def check_availability(self) -> bool:
        return _HAS_AZURE_EMBED and bool(self._api_key) and bool(self._endpoint)


# ============================================================
# OCI GenAI Embeddings — Cohere Embed (D238)
# ============================================================
try:
    import oci as _oci_embed
    _HAS_OCI_EMBED = True
except ImportError:
    _HAS_OCI_EMBED = False


class OCIEmbeddingProvider(EmbeddingProvider):
    """Oracle OCI Generative AI Cohere embedding provider."""

    def __init__(self, compartment_id: str = "",
                 model_id: str = "cohere.embed-english-v3.0",
                 service_endpoint: str = ""):
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_OCID", "")
        self._model_id = model_id
        self._endpoint = service_endpoint or os.environ.get(
            "OCI_GENAI_ENDPOINT",
            "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
        )
        self._client = None
        self._dims = 1024

    @property
    def provider_name(self) -> str:
        return "oci_genai"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _get_client(self):
        if self._client is None and _HAS_OCI_EMBED:
            try:
                config = _oci_embed.config.from_file()
                self._client = _oci_embed.generative_ai_inference.GenerativeAiInferenceClient(
                    config, service_endpoint=self._endpoint,
                )
            except Exception:
                pass
        return self._client

    def embed(self, text: str) -> Optional[List[float]]:
        client = self._get_client()
        if not client:
            return None
        try:
            req = _oci_embed.generative_ai_inference.models.EmbedTextDetails(
                inputs=[text],
                serving_mode=_oci_embed.generative_ai_inference.models.OnDemandServingMode(
                    model_id=self._model_id,
                ),
                compartment_id=self._compartment_id,
                input_type="SEARCH_DOCUMENT",
            )
            resp = client.embed_text(req)
            return resp.data.embeddings[0]
        except Exception:
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        client = self._get_client()
        if not client:
            return [None] * len(texts)
        try:
            req = _oci_embed.generative_ai_inference.models.EmbedTextDetails(
                inputs=texts,
                serving_mode=_oci_embed.generative_ai_inference.models.OnDemandServingMode(
                    model_id=self._model_id,
                ),
                compartment_id=self._compartment_id,
                input_type="SEARCH_DOCUMENT",
            )
            resp = client.embed_text(req)
            return resp.data.embeddings
        except Exception:
            return [None] * len(texts)

    def check_availability(self) -> bool:
        return _HAS_OCI_EMBED and bool(self._compartment_id)


# ============================================================
# IBM watsonx.ai Embeddings — Slate (D238)
# ============================================================
try:
    from ibm_watsonx_ai import Credentials as _IBMEmbedCreds
    from ibm_watsonx_ai.foundation_models import Embeddings as _IBMEmbeddings
    _HAS_IBM_EMBED = True
except ImportError:
    _HAS_IBM_EMBED = False


class IBMWatsonxEmbeddingProvider(EmbeddingProvider):
    """IBM watsonx.ai Slate embedding provider."""

    def __init__(self, api_key: str = "", project_id: str = "",
                 url: str = "",
                 model_id: str = "ibm/slate-125m-english-rtrvr-v2"):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._project_id = project_id or os.environ.get("IBM_WATSONX_PROJECT_ID", "")
        self._url = url or os.environ.get(
            "IBM_WATSONX_URL", "https://us-south.ml.cloud.ibm.com"
        )
        self._model_id = model_id
        self._client = None
        self._dims = 768

    @property
    def provider_name(self) -> str:
        return "ibm_watsonx"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _get_client(self):
        if self._client is None and _HAS_IBM_EMBED and self._api_key:
            creds = _IBMEmbedCreds(api_key=self._api_key, url=self._url)
            self._client = _IBMEmbeddings(
                model_id=self._model_id,
                credentials=creds,
                project_id=self._project_id,
            )
        return self._client

    def embed(self, text: str) -> Optional[List[float]]:
        client = self._get_client()
        if not client:
            return None
        try:
            result = client.embed_documents([text])
            return result[0] if result else None
        except Exception:
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        client = self._get_client()
        if not client:
            return [None] * len(texts)
        try:
            return client.embed_documents(texts)
        except Exception:
            return [None] * len(texts)

    def check_availability(self) -> bool:
        return _HAS_IBM_EMBED and bool(self._api_key) and bool(self._project_id)
