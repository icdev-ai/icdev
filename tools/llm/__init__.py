# CUI // SP-CTI
"""ICDEV LLM Provider Abstraction Layer.

Vendor-agnostic interface for LLM inference and embeddings.
Supports AWS Bedrock, Anthropic API, OpenAI API, and local models
(Ollama, vLLM) via a unified provider interface with config-driven
function-to-model routing and automatic fallback chains.

Usage::

    from tools.llm import get_router, get_embedding_provider
    from tools.llm.provider import LLMRequest

    # LLM invocation
    router = get_router()
    response = router.invoke("code_generation", LLMRequest(
        messages=[{"role": "user", "content": "Write a hello world function"}],
        max_tokens=1024,
    ))

    # Embeddings
    emb_provider = get_embedding_provider()
    vector = emb_provider.embed("search query text")
"""

from tools.llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    EmbeddingProvider,
)

_router_instance = None


def get_router(config_path=None):
    """Get the singleton LLM router instance.

    Args:
        config_path: Optional path to llm_config.yaml override.

    Returns:
        LLMRouter instance.
    """
    global _router_instance
    if _router_instance is None:
        from tools.llm.router import LLMRouter
        _router_instance = LLMRouter(config_path=config_path)
    return _router_instance


def get_embedding_provider(config_path=None):
    """Get the first available embedding provider.

    Args:
        config_path: Optional path to llm_config.yaml override.

    Returns:
        EmbeddingProvider instance.
    """
    router = get_router(config_path)
    return router.get_embedding_provider()


__all__ = [
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "EmbeddingProvider",
    "get_router",
    "get_embedding_provider",
]
