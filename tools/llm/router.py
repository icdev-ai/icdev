# CUI // SP-CTI
"""Config-driven LLM router.

Reads args/llm_config.yaml and resolves each ICDEV function to a
provider + model via fallback chain. Probes provider availability
and caches results.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse, EmbeddingProvider

logger = logging.getLogger("icdev.llm.router")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"


def _expand_env(value):
    """Expand ${VAR:-default} patterns in string values."""
    if not isinstance(value, str):
        return value
    pattern = r'\$\{([^}]+)\}'
    def replacer(match):
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var, default)
        return os.environ.get(expr, match.group(0))
    return re.sub(pattern, replacer, value)


class LLMRouter:
    """Config-driven router that maps ICDEV functions to LLM providers.

    Walks fallback chains, probes availability, and returns the first
    responsive provider + model pair.
    """

    def __init__(self, config_path=None):
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config: Dict = {}
        self._providers: Dict[str, LLMProvider] = {}
        self._embedding_providers: Dict[str, EmbeddingProvider] = {}
        self._availability_cache: Dict[str, bool] = {}
        self._availability_cache_time: float = 0.0
        self._cache_ttl: float = 1800.0

        self._load_config()

    # -------------------------------------------------------------------
    # Config loading
    # -------------------------------------------------------------------
    def _load_config(self):
        """Load and parse llm_config.yaml."""
        if yaml is None:
            logger.warning("PyYAML not available — using empty LLM config")
            self._config = {}
            return
        if not self._config_path.exists():
            logger.warning("LLM config not found at %s — using empty config", self._config_path)
            self._config = {}
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            self._cache_ttl = float(
                self._config.get("settings", {}).get(
                    "availability_cache_ttl_seconds", 1800
                )
            )
            logger.info(
                "LLM config loaded: %d providers, %d models, %d routes",
                len(self._config.get("providers", {})),
                len(self._config.get("models", {})),
                len(self._config.get("routing", {})),
            )
        except Exception as exc:
            logger.error("Failed to load LLM config: %s", exc)
            self._config = {}

    # -------------------------------------------------------------------
    # Provider instantiation (lazy)
    # -------------------------------------------------------------------
    def _get_provider(self, provider_name: str) -> Optional[LLMProvider]:
        """Get or create a provider instance by name."""
        if provider_name in self._providers:
            return self._providers[provider_name]

        provider_cfg = self._config.get("providers", {}).get(provider_name, {})
        if not provider_cfg:
            logger.warning("Provider '%s' not found in config", provider_name)
            return None

        ptype = provider_cfg.get("type", "")
        instance = None

        try:
            if ptype == "bedrock":
                from tools.llm.bedrock_provider import BedrockLLMProvider
                region = _expand_env(provider_cfg.get("region", "us-gov-west-1"))
                instance = BedrockLLMProvider(region=region)

            elif ptype == "anthropic":
                from tools.llm.anthropic_provider import AnthropicLLMProvider
                api_key_env = provider_cfg.get("api_key_env", "ANTHROPIC_API_KEY")
                api_key = os.environ.get(api_key_env, "")
                base_url = provider_cfg.get("base_url", "https://api.anthropic.com")
                instance = AnthropicLLMProvider(api_key=api_key, base_url=base_url)

            elif ptype in ("openai", "openai_compatible"):
                from tools.llm.openai_provider import OpenAICompatibleProvider
                api_key = provider_cfg.get("api_key", "")
                if not api_key:
                    api_key_env = provider_cfg.get("api_key_env", "")
                    if api_key_env:
                        api_key = os.environ.get(api_key_env, "")
                base_url = _expand_env(provider_cfg.get("base_url", "https://api.openai.com/v1"))
                instance = OpenAICompatibleProvider(
                    api_key=api_key,
                    base_url=base_url,
                    provider_label=provider_name,
                )

            else:
                logger.warning("Unknown provider type: %s", ptype)
                return None

        except ImportError as exc:
            logger.warning("Could not import provider '%s': %s", provider_name, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to create provider '%s': %s", provider_name, exc)
            return None

        if instance:
            self._providers[provider_name] = instance
            logger.debug("Created provider instance: %s (%s)", provider_name, ptype)

        return instance

    # -------------------------------------------------------------------
    # Model resolution
    # -------------------------------------------------------------------
    def _get_model_config(self, model_name: str) -> dict:
        """Get model configuration by logical name."""
        return self._config.get("models", {}).get(model_name, {})

    def _check_model_available(self, model_name: str) -> bool:
        """Check if a model is available, using cache."""
        now = time.time()
        if (now - self._availability_cache_time) > self._cache_ttl:
            self._availability_cache = {}
            self._availability_cache_time = now

        if model_name in self._availability_cache:
            return self._availability_cache[model_name]

        model_cfg = self._get_model_config(model_name)
        if not model_cfg:
            self._availability_cache[model_name] = False
            return False

        provider_name = model_cfg.get("provider", "")
        provider = self._get_provider(provider_name)
        if provider is None:
            self._availability_cache[model_name] = False
            return False

        prefer_local = self._config.get("settings", {}).get("prefer_local", False)
        if prefer_local:
            ptype = self._config.get("providers", {}).get(provider_name, {}).get("type", "")
            if ptype not in ("openai_compatible",) and provider_name not in ("ollama", "vllm"):
                # In prefer_local mode, skip cloud providers
                self._availability_cache[model_name] = False
                return False

        try:
            available = provider.check_availability(model_cfg.get("model_id", ""))
            self._availability_cache[model_name] = available
            return available
        except Exception:
            self._availability_cache[model_name] = False
            return False

    # -------------------------------------------------------------------
    # Routing
    # -------------------------------------------------------------------
    def get_provider_for_function(self, function: str) -> Tuple[Optional[LLMProvider], str, dict]:
        """Resolve function to (provider, model_id, model_config).

        Walks the fallback chain for the given function.
        Returns (None, "", {}) if no model is available.
        """
        routing = self._config.get("routing", {})
        route = routing.get(function, routing.get("default", {}))
        chain = route.get("chain", [])

        if not chain:
            logger.warning("No routing chain for function '%s'", function)
            return None, "", {}

        for model_name in chain:
            if self._check_model_available(model_name):
                model_cfg = self._get_model_config(model_name)
                provider_name = model_cfg.get("provider", "")
                provider = self._get_provider(provider_name)
                if provider:
                    logger.debug(
                        "Resolved %s -> %s (%s via %s)",
                        function, model_name, model_cfg.get("model_id"), provider_name,
                    )
                    return provider, model_cfg.get("model_id", ""), model_cfg

        # Fallback: try first model in chain without availability check
        if chain:
            model_name = chain[0]
            model_cfg = self._get_model_config(model_name)
            provider_name = model_cfg.get("provider", "")
            provider = self._get_provider(provider_name)
            if provider:
                logger.warning(
                    "No confirmed available model for '%s'; attempting %s anyway",
                    function, model_name,
                )
                return provider, model_cfg.get("model_id", ""), model_cfg

        return None, "", {}

    def get_effort(self, function: str) -> str:
        """Get configured effort level for a function."""
        routing = self._config.get("routing", {})
        route = routing.get(function, routing.get("default", {}))
        return route.get("effort", "medium")

    def invoke(self, function: str, request: LLMRequest) -> LLMResponse:
        """Resolve provider for function and invoke.

        Convenience method that combines routing + invocation.

        Args:
            function: ICDEV function name (e.g. 'code_generation', 'nlq_sql').
            request: Vendor-agnostic LLM request.

        Returns:
            LLMResponse.

        Raises:
            RuntimeError: If no provider is available for the function.
        """
        provider, model_id, model_cfg = self.get_provider_for_function(function)
        if provider is None:
            raise RuntimeError(
                "No LLM provider available for function '{}'. "
                "Check llm_config.yaml and provider credentials.".format(function)
            )

        # Apply configured effort if not set on request
        if not request.effort or request.effort == "medium":
            request.effort = self.get_effort(function)

        return provider.invoke(request, model_id, model_cfg)

    def invoke_streaming(self, function: str, request: LLMRequest):
        """Resolve provider and invoke with streaming."""
        provider, model_id, model_cfg = self.get_provider_for_function(function)
        if provider is None:
            raise RuntimeError(
                "No LLM provider available for function '{}'.".format(function)
            )
        if not request.effort or request.effort == "medium":
            request.effort = self.get_effort(function)
        return provider.invoke_streaming(request, model_id, model_cfg)

    # -------------------------------------------------------------------
    # Embedding providers
    # -------------------------------------------------------------------
    def get_embedding_provider(self) -> EmbeddingProvider:
        """Get the first available embedding provider.

        Walks the embeddings.default_chain from config.

        Raises:
            RuntimeError if no embedding provider is available.
        """
        emb_cfg = self._config.get("embeddings", {})
        chain = emb_cfg.get("default_chain", [])
        models = emb_cfg.get("models", {})

        for model_name in chain:
            if model_name in self._embedding_providers:
                return self._embedding_providers[model_name]

            mcfg = models.get(model_name, {})
            if not mcfg:
                continue

            provider_name = mcfg.get("provider", "")
            ptype = self._config.get("providers", {}).get(provider_name, {}).get("type", "")

            try:
                emb = None
                if ptype in ("openai", "openai_compatible"):
                    from tools.llm.embedding_provider import OpenAIEmbeddingProvider
                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    api_key = pcfg.get("api_key", "")
                    if not api_key:
                        api_key_env = pcfg.get("api_key_env", "")
                        if api_key_env:
                            api_key = os.environ.get(api_key_env, "")
                    base_url = _expand_env(pcfg.get("base_url", "https://api.openai.com/v1"))
                    emb = OpenAIEmbeddingProvider(
                        api_key=api_key,
                        base_url=base_url,
                        model_id=mcfg.get("model_id", "text-embedding-3-small"),
                        dims=mcfg.get("dimensions", 1536),
                    )
                elif ptype == "bedrock":
                    from tools.llm.embedding_provider import BedrockEmbeddingProvider
                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    region = _expand_env(pcfg.get("region", "us-gov-west-1"))
                    emb = BedrockEmbeddingProvider(
                        region=region,
                        model_id=mcfg.get("model_id", "amazon.titan-embed-text-v2:0"),
                        dims=mcfg.get("dimensions", 1024),
                    )

                if emb and emb.check_availability():
                    self._embedding_providers[model_name] = emb
                    logger.info("Embedding provider ready: %s", model_name)
                    return emb
            except ImportError as exc:
                logger.debug("Embedding provider '%s' not importable: %s", model_name, exc)
            except Exception as exc:
                logger.debug("Embedding provider '%s' failed: %s", model_name, exc)

        raise RuntimeError(
            "No embedding provider available. Check llm_config.yaml embeddings section."
        )

    # -------------------------------------------------------------------
    # Model pricing lookup
    # -------------------------------------------------------------------
    def get_model_pricing(self, model_id: str) -> dict:
        """Look up pricing for a model_id (searches all models)."""
        for _name, cfg in self._config.get("models", {}).items():
            if cfg.get("model_id") == model_id:
                return cfg.get("pricing", {})
        # Also check embedding models
        emb_models = self._config.get("embeddings", {}).get("models", {})
        for _name, cfg in emb_models.items():
            if cfg.get("model_id") == model_id:
                return cfg.get("pricing", {})
        return {}

    def get_all_model_pricing(self) -> Dict[str, dict]:
        """Get pricing for all configured models. Returns {model_id: pricing}."""
        result = {}
        for _name, cfg in self._config.get("models", {}).items():
            mid = cfg.get("model_id", "")
            if mid:
                result[mid] = cfg.get("pricing", {})
        emb_models = self._config.get("embeddings", {}).get("models", {})
        for _name, cfg in emb_models.items():
            mid = cfg.get("model_id", "")
            if mid:
                result[mid] = cfg.get("pricing", {})
        return result
