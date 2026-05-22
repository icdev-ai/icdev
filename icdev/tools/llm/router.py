# [TEMPLATE: CUI // SP-CTI]
"""Config-driven LLM router.

Reads args/llm_config.yaml and resolves each ICDEV™ function to a
provider + model via fallback chain. Probes provider availability
and caches results.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from icdev.tools.db.storage import get_connection
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

from icdev.tools.llm.provider import LLMProvider, LLMRequest, LLMResponse, EmbeddingProvider

logger = logging.getLogger("icdev.llm.router")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM provider in the routing chain can serve a request.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` blocks
    keep working. Callers that want to react specifically to "no LLM at
    all" environments should catch this class and degrade to deterministic
    behavior (templates, rules, cached results, or skip the step).

    Attributes:
        function: ICDEV™ function name that was requested.
        chain: List of model names that were tried (may be empty in
            no-LLM mode where probing is short-circuited).
        no_llm_mode: True when this was raised by an explicit no-LLM
            configuration (env var or config flag) rather than runtime
            probe failure.
    """

    def __init__(self, message: str, *, function: str = "", chain=None, no_llm_mode: bool = False):
        super().__init__(message)
        self.function = function
        self.chain = list(chain or [])
        self.no_llm_mode = no_llm_mode


def _expand_env(value):
    """Expand ${VAR:-default} patterns in string values."""
    if not isinstance(value, str):
        return value
    pattern = r"\$\{([^}]+)\}"

    def replacer(match):
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var, default)
        return os.environ.get(expr, match.group(0))

    return re.sub(pattern, replacer, value)


class LLMRouter:
    """Config-driven router that maps ICDEV™ functions to LLM providers.

    Walks fallback chains, probes availability, and returns the first
    responsive provider + model pair.
    """

    # Runtime dual-model toggle (None = use config/env, True/False = explicit)
    _dual_model_runtime: Optional[bool] = None

    # RL router singleton (shared across all LLMRouter instances in process)
    _rl_router_instance = None

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
    # Dual-model mode (RTX 4060 Ti 8GB VRAM — 2 models resident)
    # -------------------------------------------------------------------
    @classmethod
    def set_dual_model(cls, enabled: bool) -> None:
        """Toggle dual-model mode at runtime (e.g. from dashboard)."""
        cls._dual_model_runtime = enabled
        logger.info("Dual-model mode %s (runtime toggle)", "ENABLED" if enabled else "DISABLED")

    @classmethod
    def get_dual_model(cls) -> bool:
        """Check if dual-model mode is active."""
        if cls._dual_model_runtime is not None:
            return cls._dual_model_runtime
        env = os.environ.get("ICDEV_DUAL_MODEL", "").lower()
        return env in ("true", "1", "yes")

    @staticmethod
    def is_dual_model_active(cfg: dict) -> bool:
        """Check dual-model from config + env + runtime toggle."""
        # Runtime toggle takes priority
        if LLMRouter._dual_model_runtime is not None:
            return LLMRouter._dual_model_runtime
        # Env var next
        env = os.environ.get("ICDEV_DUAL_MODEL", "").lower()
        if env in ("true", "1", "yes"):
            return True
        if env in ("false", "0", "no"):
            return False
        # Config value (supports ${ICDEV_DUAL_MODEL:-false} expansion)
        dm = cfg.get("dual_model", {})
        val = str(dm.get("enabled", "false")).lower()
        return val in ("true", "1", "yes")

    # -------------------------------------------------------------------
    # RL router (lazy singleton)
    # -------------------------------------------------------------------
    def _get_rl_router(self):
        """Return the process-wide RLRouter singleton (lazy-init).

        Reads `rl_routing.enabled` from llm_config.yaml (default True).
        Falls back to a disabled stub on import errors so existing routing
        is never disrupted.
        """
        if LLMRouter._rl_router_instance is not None:
            return LLMRouter._rl_router_instance

        enabled = self._config.get("rl_routing", {}).get("enabled", True)
        try:
            from tools.llm.rl_router import RLRouter

            LLMRouter._rl_router_instance = RLRouter(enabled=enabled)
        except Exception as exc:
            logger.debug("RL router unavailable, using passthrough: %s", exc)

            class _NoopRL:
                def rank_models(self, fn, models):
                    return models

                def record_outcome(self, *a, **kw):
                    pass

            LLMRouter._rl_router_instance = _NoopRL()

        return LLMRouter._rl_router_instance

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
            self._cache_ttl = float(self._config.get("settings", {}).get("availability_cache_ttl_seconds", 1800))
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
    # No-LLM mode (deterministic-only environments)
    # -------------------------------------------------------------------
    # Some deployments have NO LLM at all — no Ollama, no API keys, no
    # vLLM, nothing. ICDEV™'s deterministic tools (templates, rules,
    # static analysis, compliance generation) still work in those
    # environments; the router just needs to short-circuit instead of
    # spending 30+ seconds probing every chain entry.
    #
    # Activation:
    #   1. ICDEV_NO_LLM=true env var (highest priority)
    #   2. settings.no_llm: true in args/llm_config.yaml
    #   3. Auto-detected at runtime by has_any_llm() returning False
    #
    # Process-wide cache so callers can poll cheaply.
    _no_llm_runtime_cache: Optional[bool] = None
    _no_llm_runtime_cache_time: float = 0.0
    _NO_LLM_CACHE_TTL: float = 300.0  # 5 minutes

    def is_no_llm_mode(self) -> bool:
        """Return True when this environment has no LLM available.

        Cheap check — only consults env var and config. Does NOT probe
        the network. For a probing check, use ``has_any_llm()``.
        """
        env = os.environ.get("ICDEV_NO_LLM", "").lower()
        if env in ("true", "1", "yes"):
            return True
        if env in ("false", "0", "no"):
            return False
        return bool(self._config.get("settings", {}).get("no_llm", False))

    def has_any_llm(self, refresh: bool = False) -> bool:
        """Return True if at least one model in any routing chain works.

        Walks the union of all model chains in the config and asks each
        provider whether it can serve a request. Result is cached for
        ``_NO_LLM_CACHE_TTL`` seconds so callers can poll cheaply (e.g.
        from a dashboard health check or a request preflight).

        Honors ``is_no_llm_mode()`` — when explicit no-LLM mode is set,
        returns False immediately without probing.

        Args:
            refresh: When True, ignore the cached result and re-probe.

        Returns:
            True if at least one provider+model pair is reachable.
        """
        if self.is_no_llm_mode():
            return False

        now = time.time()
        if (
            not refresh
            and LLMRouter._no_llm_runtime_cache is not None
            and (now - LLMRouter._no_llm_runtime_cache_time) < self._NO_LLM_CACHE_TTL
        ):
            return LLMRouter._no_llm_runtime_cache

        # Collect every model name referenced by any routing chain
        seen: set = set()
        for route in self._config.get("routing", {}).values():
            for model_name in route.get("chain", []) or []:
                seen.add(model_name)

        # Probe in chain-priority order: default chain first if present,
        # so common cases short-circuit fast.
        default_chain = self._config.get("routing", {}).get("default", {}).get("chain", []) or []
        ordered = list(default_chain) + [m for m in seen if m not in default_chain]

        result = False
        for model_name in ordered:
            try:
                if self._check_model_available(model_name):
                    result = True
                    break
            except Exception:
                continue

        LLMRouter._no_llm_runtime_cache = result
        LLMRouter._no_llm_runtime_cache_time = now
        if not result:
            logger.warning(
                "has_any_llm: no provider/model pair is reachable across %d configured models — "
                "ICDEV™ will run in no-LLM (deterministic-only) mode",
                len(ordered),
            )
        return result

    @classmethod
    def clear_no_llm_cache(cls) -> None:
        """Clear the cached has_any_llm() result (e.g. after enabling Ollama)."""
        cls._no_llm_runtime_cache = None
        cls._no_llm_runtime_cache_time = 0.0

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

            elif ptype == "ollama":
                from tools.llm.ollama_provider import OllamaProvider

                base_url = _expand_env(provider_cfg.get("base_url", "http://localhost:11434"))
                instance = OllamaProvider(base_url=base_url)

            elif ptype == "gemini":
                from tools.llm.gemini_provider import GeminiProvider

                api_key = provider_cfg.get("api_key", "")
                if not api_key:
                    api_key_env = provider_cfg.get("api_key_env", "GOOGLE_API_KEY")
                    if api_key_env:
                        api_key = os.environ.get(api_key_env, "")
                instance = GeminiProvider(api_key=api_key)

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

            elif ptype == "azure_openai":
                from tools.llm.azure_openai_provider import AzureOpenAIProvider

                endpoint = _expand_env(provider_cfg.get("endpoint", ""))
                api_key = _expand_env(provider_cfg.get("api_key", ""))
                if not api_key:
                    api_key_env = provider_cfg.get("api_key_env", "AZURE_OPENAI_API_KEY")
                    api_key = os.environ.get(api_key_env, "")
                api_version = provider_cfg.get("api_version", "2024-06-01")
                instance = AzureOpenAIProvider(
                    endpoint=endpoint,
                    api_key=api_key,
                    api_version=api_version,
                )

            elif ptype == "vertex_ai":
                from tools.llm.vertex_ai_provider import VertexAIProvider

                project_id = _expand_env(provider_cfg.get("project_id", ""))
                location = provider_cfg.get("location", "us-east4")
                instance = VertexAIProvider(
                    project_id=project_id,
                    location=location,
                )

            elif ptype == "oci_genai":
                from tools.llm.oci_genai_provider import OCIGenAIProvider

                compartment_id = _expand_env(provider_cfg.get("compartment_id", ""))
                serving_mode = provider_cfg.get("serving_mode", "ON_DEMAND")
                instance = OCIGenAIProvider(
                    compartment_id=compartment_id,
                    serving_mode=serving_mode,
                )

            elif ptype == "ibm_watsonx":
                from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider

                api_key = _expand_env(provider_cfg.get("api_key", ""))
                if not api_key:
                    api_key_env = provider_cfg.get("api_key_env", "IBM_CLOUD_API_KEY")
                    api_key = os.environ.get(api_key_env, "")
                project_id = _expand_env(provider_cfg.get("project_id", ""))
                if not project_id:
                    project_id = os.environ.get("IBM_WATSONX_PROJECT_ID", "")
                url = _expand_env(provider_cfg.get("url", ""))
                if not url:
                    url = os.environ.get("IBM_WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
                instance = IBMWatsonxProvider(
                    api_key=api_key,
                    project_id=project_id,
                    url=url,
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
                        function,
                        model_name,
                        model_cfg.get("model_id"),
                        provider_name,
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
                    function,
                    model_name,
                )
                return provider, model_cfg.get("model_id", ""), model_cfg

        return None, "", {}

    def get_effort(self, function: str) -> str:
        """Get configured effort level for a function."""
        routing = self._config.get("routing", {})
        route = routing.get(function, routing.get("default", {}))
        return route.get("effort", "medium")

    def _get_chain_for_function(self, function: str) -> list:
        """Get the model chain for a function."""
        routing = self._config.get("routing", {})
        route = routing.get(function, routing.get("default", {}))
        return route.get("chain", [])

    def _log_telemetry(
        self,
        function: str,
        request,
        response,
        model_id: str,
        provider_name: str,
        latency_ms: int,
    ) -> None:
        """Log AI interaction to ai_telemetry table (D218)."""
        import hashlib

        prompt_text = " ".join(m.get("content", "") for m in (request.messages or []) if isinstance(m, dict))
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8", errors="replace")).hexdigest()[:32]
        response_hash = hashlib.sha256(
            (getattr(response, "content", "") or "").encode("utf-8", errors="replace")
        ).hexdigest()[:32]

        try:
            from tools.security.ai_telemetry_logger import AITelemetryLogger

            logger_inst = AITelemetryLogger()
            logger_inst.log_ai_interaction(
                model_id=getattr(response, "model_id", model_id) or model_id,
                provider=provider_name,
                prompt_hash=prompt_hash,
                response_hash=response_hash,
                input_tokens=getattr(response, "input_tokens", 0) or 0,
                output_tokens=getattr(response, "output_tokens", 0) or 0,
                thinking_tokens=getattr(response, "thinking_tokens", 0) or 0,
                latency_ms=float(latency_ms),
                cost_usd=getattr(response, "cost_usd", 0.0) or 0.0,
                project_id=getattr(request, "project_id", None),
                function=function,
                api_key_source=getattr(request, "api_key_source", "system") or "system",
            )
        except Exception:
            pass

    def _scan_for_injection(self, request: LLMRequest) -> Optional[str]:
        """Scan request messages for prompt injection patterns.

        Returns action string ('block', 'flag', 'warn', 'allow') or None
        if scanner is unavailable. Graceful import — does not fail if
        prompt_injection_detector is not importable.
        """
        try:
            from tools.security.prompt_injection_detector import PromptInjectionDetector
        except ImportError:
            return None

        detector = PromptInjectionDetector()
        # Scan all user messages in the request
        texts = []
        for msg in request.messages or []:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    texts.append(content)

        if not texts:
            return "allow"

        combined = "\n".join(texts)
        result = detector.scan_text(combined, source="llm_router")

        if result["detected"]:
            logger.warning(
                "Prompt injection detected in LLM request: confidence=%.2f action=%s findings=%d",
                result["confidence"],
                result["action"],
                result["finding_count"],
            )
            # Log to DB (best-effort)
            detector.log_detection(
                result,
                project_id=request.project_id,
                user_id=None,
            )

        return result["action"]

    # -------------------------------------------------------------------
    # D-RDT-1: Pre-invoke redaction hook (all modules, all LLM calls)
    # -------------------------------------------------------------------

    # Singleton sanitizer — avoid re-creating on every invoke() call (D-RDT-9)
    _redaction_sanitizer = None
    _redaction_sanitizer_ts = 0.0
    _REDACTION_CACHE_TTL = 1800  # 30 minutes

    def _get_sanitizer(self):
        """Get or create cached GovConSanitizer singleton."""
        import time as _t

        now = _t.time()
        if (
            LLMRouter._redaction_sanitizer is not None
            and (now - LLMRouter._redaction_sanitizer_ts) < self._REDACTION_CACHE_TTL
        ):
            return LLMRouter._redaction_sanitizer
        try:
            from tools.redaction.govcon_sanitizer import GovConSanitizer

            LLMRouter._redaction_sanitizer = GovConSanitizer()
            LLMRouter._redaction_sanitizer_ts = now
            return LLMRouter._redaction_sanitizer
        except ImportError:
            return None

    def _pre_invoke_redaction(self, function: str, request: LLMRequest) -> Optional[str]:
        """Sanitize PII in request messages before sending to any LLM.

        Applies to ALL modules. Checks if the function is in the enforced
        scope and whether routing is local-only (skips if configured).

        Returns session_id for de-anonymization, or None if skipped.
        """
        # D-RDT-4: Config toggle — skip redaction if explicitly disabled
        rdcfg = self._config.get("redaction", {})
        if not rdcfg.get("enabled", True):
            return None

        # D-RDT-5: Skip redaction for excluded functions (e.g. Pulse articles
        # are public blog posts — redacting org names produces [ORGANIZATION]
        # tokens that leak into published content).
        excluded = rdcfg.get("excluded_functions", [])
        if function in excluded:
            return None

        sanitizer = self._get_sanitizer()
        if sanitizer is None:
            return None

        try:
            # Check if routing is local-only for this function
            chain = self._get_chain_for_function(function)
            is_local = all(
                self._get_model_config(m).get("provider") == "ollama" for m in chain if self._get_model_config(m)
            )

            # Extract text from messages
            for msg in request.messages or []:
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        sanitized, meta = sanitizer.sanitize_for_llm(
                            content,
                            function_name=function,
                            impact_level=request.classification or "IL4",
                            is_local_only=is_local,
                        )
                        if not meta.get("skipped", True):
                            msg["content"] = sanitized

            # Also sanitize system_prompt if present
            if request.system_prompt and request.system_prompt.strip():
                sanitized, meta = sanitizer.sanitize_for_llm(
                    request.system_prompt,
                    function_name=function,
                    impact_level=request.classification or "IL4",
                    is_local_only=is_local,
                )
                if not meta.get("skipped", True):
                    request.system_prompt = sanitized

            return sanitizer.session_id

        except Exception as exc:
            logger.debug("Pre-invoke redaction failed (non-blocking): %s", exc)
            return None

    def _post_invoke_deanonymize(self, response, redaction_session: Optional[str]):
        """Restore original values in LLM response using redaction registry.

        Round-trip de-anonymization: surrogates inserted by _pre_invoke_redaction
        are replaced with original values so the caller sees real data.
        Only operates when redaction_session is not None (redaction was applied).
        """
        if not redaction_session:
            return response
        # Check config toggle
        rdcfg = self._config.get("redaction", {})
        if not rdcfg.get("deanonymize_response", True):
            return response
        try:
            sanitizer = self._get_sanitizer()
            if sanitizer is None:
                return response
            # De-anonymize text content in response
            if hasattr(response, "text") and response.text:
                response.text = sanitizer.de_anonymize_response(response.text)
            elif hasattr(response, "content") and isinstance(response.content, str):
                response.content = sanitizer.de_anonymize_response(response.content)
            elif isinstance(response, dict):
                for key in ("text", "content"):
                    if key in response and isinstance(response[key], str):
                        response[key] = sanitizer.de_anonymize_response(response[key])
        except Exception as exc:
            logger.debug("Post-invoke de-anonymization failed (non-blocking): %s", exc)
        return response

    def _audit_redaction(
        self,
        function: str,
        redaction_session: Optional[str],
        detection_count: int = 0,
        entity_types: Optional[list] = None,
        impact_level: str = "IL4",
    ):
        """Log redaction event to append-only redaction_audit table."""
        if not redaction_session or detection_count == 0:
            return
        rdcfg = self._config.get("redaction", {})
        if not rdcfg.get("audit_enabled", True):
            return
        try:
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO redaction_audit
                    (id, session_id, function, detection_count,
                     entity_types_json, impact_level, action, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    f"raud-{redaction_session[:8]}-{function[:20]}",
                    redaction_session,
                    function,
                    detection_count,
                    json.dumps(entity_types or []),
                    impact_level,
                    "redacted",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.debug("Redaction audit log failed (non-blocking): %s", exc)

    # -------------------------------------------------------------------
    # Two-tier routing helpers (D-TT1: qwen3 worker → Claude planner)
    # -------------------------------------------------------------------

    def _invoke_model_direct(self, model_name: str, request: LLMRequest) -> Optional[LLMResponse]:
        """Invoke a specific named model without chain fallback.

        Returns None on any error so callers can fall through to chain.
        """
        model_cfg = self._get_model_config(model_name)
        if not model_cfg:
            logger.warning("Two-tier: model '%s' not found in config", model_name)
            return None
        provider_name = model_cfg.get("provider", "")
        provider = self._get_provider(provider_name)
        if provider is None:
            logger.warning("Two-tier: provider '%s' unavailable for model '%s'", provider_name, model_name)
            return None
        model_id = model_cfg.get("model_id", "")
        try:
            return provider.invoke(request, model_id, model_cfg)
        except Exception as exc:
            logger.warning("Two-tier: direct invoke failed for %s/%s: %s", model_name, model_id, exc)
            return None

    @staticmethod
    def _sanitize_rag_chunk(text: str) -> str:
        """Remove known prompt injection patterns from RAG chunk content.

        SEC: Mitigates indirect prompt injection — attackers could embed
        instructions in documents that get retrieved and injected into
        the LLM system prompt. This strips common injection patterns.
        """
        import re as _re

        # Patterns that attempt to override system behavior
        _injection_patterns = [
            _re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions?"),
            _re.compile(r"(?i)you\s+are\s+now\s+(?:a|an|in)\s+"),
            _re.compile(r"(?i)system\s*:\s*"),
            _re.compile(r"(?i)<<\s*SYS\s*>>"),
            _re.compile(r"(?i)\[INST\]"),
            _re.compile(r"(?i)forget\s+(?:all|everything|your)\s+"),
            _re.compile(r"(?i)new\s+instructions?\s*:"),
            _re.compile(r"(?i)override\s+(?:previous|all|system)"),
        ]
        sanitized = text
        for pattern in _injection_patterns:
            sanitized = pattern.sub("[FILTERED]", sanitized)
        return sanitized

    def _rag_augment(self, request: LLMRequest, function: str) -> LLMRequest:
        """Prepend RAG context to request system prompt (D-RAG-2, D-RAG-21).

        Graceful import: does nothing if RAG subsystem unavailable.
        RAG context goes into the system prompt of _draft_request() so
        qwen3 produces a better draft; Claude reviews the draft without
        seeing raw chunks.  Maximum token savings.

        D-RAG-21: When citation_enabled=true, chunks are tagged as [SOURCE-N]
        and a citation instruction is appended from hardprompts/rag_citation.md.

        Args:
            request: Original LLM request.
            function: ICDEV™ function name (checked against denylist).

        Returns:
            Augmented LLMRequest (or original if RAG unavailable/disabled).
        """
        try:
            from tools.rag.retriever import RAGRetriever
        except ImportError:
            return request  # RAG subsystem not installed

        # Check if RAG injection is enabled
        rag_cfg = self._config.get("rag", {})
        injection_cfg = rag_cfg.get("injection", {})
        if not rag_cfg.get("enabled", False) or not injection_cfg.get("enabled", True):
            return request

        # Check function denylist
        denylist = injection_cfg.get("function_denylist", [])
        if function in denylist:
            return request

        # Extract user query from messages
        query = ""
        for msg in request.messages or []:
            if isinstance(msg, dict) and msg.get("role") == "user":
                c = msg.get("content", "")
                query = c if isinstance(c, str) else str(c)
                break
        if not query:
            return request

        try:
            retriever = RAGRetriever()
            top_k = injection_cfg.get("injection_top_k", 5)
            max_chars = injection_cfg.get("max_injection_chars", 4000)
            citation_enabled = injection_cfg.get("citation_enabled", True)
            citation_instruction = injection_cfg.get("citation_instruction", True)

            results = retriever.search(query=query, top_k=top_k)
            if not results:
                return request

            # Build context block with optional [SOURCE-N] tags (D-RAG-21)
            # SEC: Sanitize chunk content to mitigate indirect prompt injection
            context_parts = []
            total_chars = 0
            for i, r in enumerate(results):
                snippet = (
                    r.content[: max_chars - total_chars] if total_chars + len(r.content) > max_chars else r.content
                )
                # SEC: Strip known prompt injection patterns from retrieved chunks
                snippet = self._sanitize_rag_chunk(snippet)
                source_label = f"{r.source_type}"
                if r.source_id:
                    source_label += f":{r.source_id}"
                if citation_enabled:
                    tag = f"[SOURCE-{i + 1}]"
                    context_parts.append(f"{tag} ({source_label} | score={r.final_score:.2f})\n{snippet}")
                else:
                    context_parts.append(f"[{source_label} | score={r.final_score:.2f}]\n{snippet}")
                total_chars += len(snippet)
                if total_chars >= max_chars:
                    break

            if not context_parts:
                return request

            context_block = (
                "\n[RELEVANT CONTEXT — retrieved from ICDEV™ knowledge base]\n"
                + "\n---\n".join(context_parts)
                + "\n[END CONTEXT]\n"
            )

            # Append citation instruction if enabled (D-RAG-21)
            citation_block = ""
            if citation_enabled and citation_instruction:
                citation_path = Path(__file__).resolve().parent.parent.parent / "hardprompts" / "rag_citation.md"
                if citation_path.exists():
                    try:
                        citation_block = "\n" + citation_path.read_text(encoding="utf-8") + "\n"
                    except Exception:
                        pass

            # Prepend to system prompt
            req = copy.copy(request)
            req.system_prompt = context_block + citation_block + (request.system_prompt or "")
            logger.debug(
                "RAG augment: injected %d chunks (%d chars, citations=%s) for %s",
                len(context_parts),
                total_chars,
                citation_enabled,
                function,
            )
            return req

        except Exception as exc:
            logger.debug("RAG augment skipped for %s: %s", function, exc)
            return request  # Never fail the main pipeline

    def _draft_request(self, request: LLMRequest) -> LLMRequest:
        """Return a copy of request with a compact-output instruction appended.

        Instructs qwen3 to produce a short, structured draft — the key to
        keeping Claude's review input token count LOW vs Claude doing the
        full task alone.
        """
        req = copy.copy(request)
        compact = (
            "\n\n[DRAFT MODE] Produce a COMPACT, structured response: bullet points, "
            "short sentences, no step-by-step reasoning chains. Max ~400 words. "
            "This draft will be reviewed and finalized by another model."
        )
        req.system_prompt = (request.system_prompt or "") + compact
        return req

    def _review_request(self, original: LLMRequest, draft: LLMResponse, function: str) -> LLMRequest:
        """Build a Claude review request from the original task + qwen3 draft.

        Claude receives: compact review system prompt + original task + draft.
        This is intentionally smaller than Claude handling the full task alone.
        """
        req = copy.copy(original)
        req.system_prompt = (
            f"You are reviewing a draft from a local AI assistant (function: {function}). "
            "Verify correctness, fix errors, fill gaps, and return the final polished response. "
            "Be direct — do not explain what you changed."
        )
        # Extract original user message for context
        original_task = ""
        for msg in original.messages or []:
            if isinstance(msg, dict) and msg.get("role") == "user":
                c = msg.get("content", "")
                original_task = c if isinstance(c, str) else str(c)
                break
        req.messages = [
            {
                "role": "user",
                "content": (
                    f"ORIGINAL TASK:\n{original_task}\n\n"
                    f"DRAFT TO REVIEW:\n{draft.content}\n\n"
                    "Return the corrected, final response only."
                ),
            }
        ]
        return req

    # -------------------------------------------------------------------
    # Fine-tuned model override (D-FT-6)
    # -------------------------------------------------------------------
    def _check_finetuned_override(
        self,
        function: str,
        tenant_id: str = "",
        project_id: str = "",
    ) -> Optional[str]:
        """Check if a fine-tuned model is active for this function (D-FT-6).

        Queries ft_active_models for a promoted model version. Returns
        the Ollama model name if found, else None.

        This is an additive lookup — if no fine-tuned model is active,
        returns None and caller falls through to default routing.
        """
        db_path = BASE_DIR / "data" / "icdev.db"
        if not db_path.exists():
            return None

        try:
            conn = get_connection(db_path=str(db_path))
            row = conn.execute(
                """SELECT ollama_model_name FROM ft_active_models
                   WHERE function_name = ? AND deactivated_at IS NULL
                   AND (tenant_id = ? OR tenant_id = '')
                   AND (project_id = ? OR project_id = '')
                   ORDER BY id DESC LIMIT 1""",
                (function, tenant_id, project_id),
            ).fetchone()
            conn.close()
            if row and row["ollama_model_name"]:
                logger.info(
                    "Fine-tuned override: %s → %s",
                    function,
                    row["ollama_model_name"],
                )
                return row["ollama_model_name"]
        except Exception as exc:
            logger.debug("Fine-tuned override check failed: %s", exc)

        return None

    def _invoke_finetuned_model(
        self,
        ollama_model_name: str,
        request: LLMRequest,
    ) -> Optional[LLMResponse]:
        """Invoke a fine-tuned model via the Ollama provider.

        Returns None on failure so caller can fall through to default.
        """
        # Find the Ollama provider instance
        provider = self._get_provider("ollama")
        if provider is None:
            # Try to find any ollama-type provider
            for pname, pcfg in self._config.get("providers", {}).items():
                if pcfg.get("type") == "ollama":
                    provider = self._get_provider(pname)
                    if provider:
                        break
        if provider is None:
            logger.warning("Fine-tuned invoke: no Ollama provider available")
            return None

        try:
            # Use the fine-tuned model name directly as model_id
            model_cfg = {"model_id": ollama_model_name, "provider": "ollama"}
            return provider.invoke(request, ollama_model_name, model_cfg)
        except Exception as exc:
            logger.warning(
                "Fine-tuned invoke failed for %s: %s",
                ollama_model_name,
                exc,
            )
            return None

    def _maybe_invoke_two_tier(self, function: str, request: LLMRequest) -> Optional[LLMResponse]:
        """Apply two-tier routing if function is configured for it.

        Returns LLMResponse if two-tier handled the call, else None
        (caller falls through to normal chain-based routing).

        Three paths:
          planner_functions  → Claude directly (no qwen3 pre-step)
          worker_functions   → qwen3 compact draft → Claude review
          scanner_functions  → qwen3 only (no review)
        """
        cfg = self._config.get("two_tier", {})
        # Allow env var override: LLM_TWO_TIER_ENABLED=false disables two-tier
        env_enabled = os.environ.get("LLM_TWO_TIER_ENABLED", "").lower()
        if env_enabled in ("false", "0", "no"):
            return None
        if not cfg.get("enabled", False) and env_enabled not in ("true", "1", "yes"):
            return None

        tier1 = _expand_env(cfg.get("tier1_model", "qwen3-local"))
        tier2 = _expand_env(cfg.get("tier2_model", "claude-sonnet"))
        planners = cfg.get("planner_functions", [])
        workers = cfg.get("worker_functions", [])
        scanners = cfg.get("scanner_functions", [])

        # Dual-model mode: swap tier1 for smaller model to fit 2 models in VRAM
        if self.is_dual_model_active(cfg):
            dm = cfg.get("dual_model", {})
            override_tier1 = dm.get("tier1_override")
            if override_tier1:
                tier1 = override_tier1
                logger.debug("Dual-model active: tier1 swapped to %s", tier1)

        if function in planners:
            # Claude plans directly
            logger.debug("Two-tier: %s → planner (Claude direct)", function)
            result = self._invoke_model_direct(tier2, request)
            if result is not None:
                return result
            # Fall through to chain on failure

        elif function in workers:
            # D-FT-6: Check if a fine-tuned model overrides tier1 for this function
            ft_override = self._check_finetuned_override(
                function,
                tenant_id=getattr(request, "tenant_id", "") or "",
                project_id=getattr(request, "project_id", "") or "",
            )

            # RAG augment: inject relevant context before drafting (D-RAG-2)
            augmented = self._rag_augment(request, function)

            if ft_override:
                # Fine-tuned model replaces qwen3 as drafter
                logger.debug(
                    "Two-tier: %s → worker (fine-tuned %s draft → Claude review)",
                    function,
                    ft_override,
                )
                draft = self._invoke_finetuned_model(
                    ft_override,
                    self._draft_request(augmented),
                )
            else:
                # Default: qwen3 drafts
                logger.debug("Two-tier: %s → worker (qwen3 draft → Claude review)", function)
                draft = self._invoke_model_direct(tier1, self._draft_request(augmented))

            if draft is not None:
                review_req = self._review_request(request, draft, function)
                reviewed = self._invoke_model_direct(tier2, review_req)
                if reviewed is not None:
                    # Store draft on response for audit/observability
                    reviewed.draft_content = draft.content  # type: ignore[attr-defined]
                    if ft_override:
                        reviewed.ft_model_used = ft_override  # type: ignore[attr-defined]
                    return reviewed
                # Claude unavailable — return draft as fallback
                logger.warning("Two-tier: Claude review unavailable for %s, returning draft", function)
                return draft
            # Drafter unavailable — fall through to chain

        elif function in scanners:
            # Check for per-function model override
            overrides = cfg.get("function_model_overrides", {})
            # Dual-model mode: merge dual overrides on top of base overrides
            if self.is_dual_model_active(cfg):
                dm_overrides = cfg.get("dual_model", {}).get("function_overrides", {})
                overrides = {**overrides, **dm_overrides}
            scanner_model = overrides.get(function, tier1)
            logger.debug("Two-tier: %s → scanner (%s only)", function, scanner_model)
            result = self._invoke_model_direct(scanner_model, request)
            if result is not None:
                return result
            # Fall through to chain on failure

        return None  # Not in two_tier config or model unavailable → use chain

    def invoke(self, function: str, request: LLMRequest) -> LLMResponse:
        """Resolve provider for function and invoke with fallback.

        Walks the full fallback chain: if the first provider fails at
        invocation time (e.g. missing credentials, network error), tries
        the next model in the chain rather than raising immediately.

        Args:
            function: ICDEV™ function name (e.g. 'code_generation', 'nlq_sql').
            request: Vendor-agnostic LLM request.

        Returns:
            LLMResponse.

        Raises:
            LLMUnavailableError: If no provider in the chain can serve the
                request. Subclass of ``RuntimeError`` so existing
                ``except RuntimeError`` blocks remain compatible. Callers
                that need to degrade to deterministic behavior should
                catch ``LLMUnavailableError`` specifically.
        """
        # Explicit no-LLM mode: short-circuit before any probing or budget
        # checks so deterministic-only environments don't pay the network
        # round-trip cost on every call.
        if self.is_no_llm_mode():
            raise LLMUnavailableError(
                "ICDEV_NO_LLM is set — LLM invocation is disabled. "
                "Tool '{}' must use its deterministic fallback path.".format(function),
                function=function,
                chain=self._get_chain_for_function(function),
                no_llm_mode=True,
            )

        # Token budget enforcement (D-BUD-1: Paperclip-inspired per-agent hard-stops)
        if request.agent_id:
            try:
                from tools.agent.token_tracker import check_budget, BudgetExceededError

                budget = check_budget(request.agent_id)
                if budget["action"] == "block":
                    raise BudgetExceededError(request.agent_id, budget)
                if budget["action"] == "warn":
                    logger.warning("Budget warning for %s: %s", request.agent_id, budget["message"])
            except ImportError:
                pass  # token_tracker not available — skip budget check
            except BudgetExceededError:
                raise  # re-raise budget errors
            except Exception as exc:
                logger.debug("Budget check failed (non-blocking): %s", exc)

        # Scan for prompt injection before invoking (D217)
        # Skip for trusted internal pipeline calls (e.g. Pulse draft with topic seeds)
        if not request.skip_injection_scan:
            injection_action = self._scan_for_injection(request)
            if injection_action == "block":
                raise RuntimeError(
                    "Prompt injection detected with high confidence — request blocked. "
                    "Review the input content for injection patterns."
                )

        # D-RDT-1: Pre-invoke redaction — sanitize PII before sending to LLM
        # Applies to ALL modules. Skips for local-only routing if configured.
        _redaction_session = self._pre_invoke_redaction(function, request)

        # Apply configured effort if not set on request
        if not request.effort or request.effort == "medium":
            request.effort = self.get_effort(function)

        # Two-tier routing: qwen3 worker → Claude planner/reviewer
        two_tier_result = self._maybe_invoke_two_tier(function, request)
        if two_tier_result is not None:
            return two_tier_result

        chain = self._get_chain_for_function(function)
        # RL routing: reorder chain by learned Q-values (epsilon-greedy)
        chain = self._get_rl_router().rank_models(function, chain)
        last_error = None

        # D286: Create trace span for LLM invocation
        try:
            from tools.observability import get_tracer

            tracer = get_tracer()
        except ImportError:
            tracer = None

        for model_name in chain:
            model_cfg = self._get_model_config(model_name)
            if not model_cfg:
                continue
            provider_name = model_cfg.get("provider", "")
            provider = self._get_provider(provider_name)
            if provider is None:
                continue
            model_id = model_cfg.get("model_id", "")

            # D286: Span with GenAI semantic conventions
            span = None
            if tracer:
                span = tracer.start_span(
                    "gen_ai.invoke",
                    kind="CLIENT",
                    attributes={
                        "gen_ai.operation.name": "chat",
                        "gen_ai.system": provider_name,
                        "gen_ai.request.model": model_id,
                        "gen_ai.effort": request.effort or "medium",
                        "icdev.llm_function": function,
                    },
                )

            try:
                import time as _time

                _start = _time.time()
                # Stamp route-level config onto request so providers can read flags like disable_thinking
                route_cfg = self._config.get("routing", {}).get(function, {})
                request._route_config = route_cfg
                response = provider.invoke(request, model_id, model_cfg)
                _latency = int((_time.time() - _start) * 1000)

                if span:
                    span.set_attribute("gen_ai.response.model", getattr(response, "model_id", model_id))
                    span.set_attribute("gen_ai.usage.input_tokens", getattr(response, "input_tokens", 0))
                    span.set_attribute("gen_ai.usage.output_tokens", getattr(response, "output_tokens", 0))
                    span.set_attribute("gen_ai.latency_ms", _latency)
                    if hasattr(response, "cost_usd"):
                        span.set_attribute("gen_ai.usage.cost_usd", response.cost_usd)
                    span.set_status("OK")
                    span.end()

                # D218: Log AI telemetry for usage dashboard
                try:
                    self._log_telemetry(
                        function=function,
                        request=request,
                        response=response,
                        model_id=model_id,
                        provider_name=provider_name,
                        latency_ms=_latency,
                    )
                except Exception:
                    pass  # Best-effort — never block on telemetry

                # D-RDT-2: Post-invoke de-anonymization — restore originals
                response = self._post_invoke_deanonymize(response, _redaction_session)

                # RL: record success so this model's Q-value improves
                self._get_rl_router().record_outcome(function, model_name, success=True, latency_ms=_latency)

                return response
            except Exception as exc:
                logger.warning(
                    "Provider %s (%s) failed for %s: %s — trying next in chain",
                    provider_name,
                    model_id,
                    function,
                    exc,
                )
                if span:
                    span.set_status("ERROR", str(exc))
                    span.add_event(
                        "provider_fallback",
                        {
                            "failed_provider": provider_name,
                            "failed_model": model_id,
                            "error": str(exc),
                        },
                    )
                    span.end()
                last_error = exc
                # Mark model as unavailable in cache so next call skips it
                self._availability_cache[model_name] = False
                # RL: record failure so this model's Q-value decreases
                self._get_rl_router().record_outcome(function, model_name, success=False)
                continue

        raise LLMUnavailableError(
            "All providers in chain {} failed for function '{}'. Last error: {}".format(chain, function, last_error),
            function=function,
            chain=chain,
            no_llm_mode=False,
        )

    def invoke_streaming(self, function: str, request: LLMRequest):
        """Resolve provider and invoke with streaming + fallback."""
        # Explicit no-LLM mode: short-circuit so streaming UIs can render
        # a "no LLM available" placeholder instead of hanging on probes.
        if self.is_no_llm_mode():
            raise LLMUnavailableError(
                "ICDEV_NO_LLM is set — streaming LLM invocation is disabled. "
                "Tool '{}' must use its deterministic fallback path.".format(function),
                function=function,
                chain=self._get_chain_for_function(function),
                no_llm_mode=True,
            )

        # D-RDT-3: Pre-invoke redaction for streaming path (parity with invoke)
        _redaction_session = self._pre_invoke_redaction(function, request)

        if not request.effort or request.effort == "medium":
            request.effort = self.get_effort(function)

        chain = self._get_chain_for_function(function)
        last_error = None

        for model_name in chain:
            model_cfg = self._get_model_config(model_name)
            if not model_cfg:
                continue
            provider_name = model_cfg.get("provider", "")
            provider = self._get_provider(provider_name)
            if provider is None:
                continue
            model_id = model_cfg.get("model_id", "")
            try:
                return provider.invoke_streaming(request, model_id, model_cfg)
            except Exception as exc:
                logger.warning(
                    "Streaming provider %s (%s) failed for %s: %s — trying next",
                    provider_name,
                    model_id,
                    function,
                    exc,
                )
                last_error = exc
                self._availability_cache[model_name] = False
                continue

        raise LLMUnavailableError(
            "All streaming providers in chain {} failed for function '{}'. Last error: {}".format(
                chain, function, last_error
            ),
            function=function,
            chain=chain,
            no_llm_mode=False,
        )

    # -------------------------------------------------------------------
    # Embedding providers
    # -------------------------------------------------------------------
    def get_embedding_provider(self) -> EmbeddingProvider:
        """Get the first available embedding provider.

        Walks the embeddings.default_chain from config.

        Raises:
            LLMUnavailableError if no embedding provider is available
            (subclass of RuntimeError for backward compatibility). In
            no-LLM environments, callers should fall back to BM25-only
            keyword search instead of hybrid semantic+keyword.
        """
        # Explicit no-LLM mode: short-circuit before probing.
        if self.is_no_llm_mode():
            raise LLMUnavailableError(
                "ICDEV_NO_LLM is set — embedding provider is disabled. "
                "Use BM25/keyword-only search as the fallback.",
                function="embeddings",
                chain=self._config.get("embeddings", {}).get("default_chain", []),
                no_llm_mode=True,
            )

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
                elif ptype == "gemini":
                    from tools.llm.embedding_provider import GeminiEmbeddingProvider

                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    api_key = pcfg.get("api_key", "")
                    if not api_key:
                        api_key_env = pcfg.get("api_key_env", "GOOGLE_API_KEY")
                        api_key = os.environ.get(api_key_env, "")
                    emb = GeminiEmbeddingProvider(
                        api_key=api_key,
                        model_id=mcfg.get("model_id", "text-embedding-004"),
                        dims=mcfg.get("dimensions", 768),
                    )

                elif ptype == "azure_openai":
                    from tools.llm.embedding_provider import AzureEmbeddingProvider

                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    api_key = _expand_env(pcfg.get("api_key", ""))
                    if not api_key:
                        api_key_env = pcfg.get("api_key_env", "AZURE_OPENAI_API_KEY")
                        api_key = os.environ.get(api_key_env, "")
                    endpoint = _expand_env(pcfg.get("endpoint", ""))
                    if not endpoint:
                        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
                    api_version = pcfg.get("api_version", "2024-02-01")
                    emb = AzureEmbeddingProvider(
                        api_key=api_key,
                        endpoint=endpoint,
                        api_version=api_version,
                        deployment=mcfg.get("model_id", "text-embedding-ada-002"),
                    )

                elif ptype == "oci_genai":
                    from tools.llm.embedding_provider import OCIEmbeddingProvider

                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    compartment_id = _expand_env(pcfg.get("compartment_id", ""))
                    if not compartment_id:
                        compartment_id = os.environ.get("OCI_COMPARTMENT_OCID", "")
                    service_endpoint = _expand_env(pcfg.get("service_endpoint", ""))
                    if not service_endpoint:
                        service_endpoint = os.environ.get(
                            "OCI_GENAI_ENDPOINT",
                            "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
                        )
                    emb = OCIEmbeddingProvider(
                        compartment_id=compartment_id,
                        model_id=mcfg.get("model_id", "cohere.embed-english-v3.0"),
                        service_endpoint=service_endpoint,
                    )

                elif ptype == "ibm_watsonx":
                    from tools.llm.embedding_provider import IBMWatsonxEmbeddingProvider

                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    api_key = _expand_env(pcfg.get("api_key", ""))
                    if not api_key:
                        api_key = os.environ.get(pcfg.get("api_key_env", "IBM_CLOUD_API_KEY"), "")
                    project_id = _expand_env(pcfg.get("project_id", ""))
                    if not project_id:
                        project_id = os.environ.get("IBM_WATSONX_PROJECT_ID", "")
                    url = _expand_env(pcfg.get("url", ""))
                    if not url:
                        url = os.environ.get("IBM_WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
                    emb = IBMWatsonxEmbeddingProvider(
                        api_key=api_key,
                        project_id=project_id,
                        url=url,
                    )

                elif ptype == "ollama":
                    from tools.llm.embedding_provider import OllamaEmbeddingProvider

                    pcfg = self._config.get("providers", {}).get(provider_name, {})
                    base_url = _expand_env(pcfg.get("base_url", "http://localhost:11434"))
                    emb = OllamaEmbeddingProvider(
                        base_url=base_url,
                        model_id=mcfg.get("model_id", "nomic-embed-text"),
                        dims=mcfg.get("dimensions", 768),
                    )

                if emb and emb.check_availability():
                    self._embedding_providers[model_name] = emb
                    logger.info("Embedding provider ready: %s", model_name)
                    return emb
            except ImportError as exc:
                logger.debug("Embedding provider '%s' not importable: %s", model_name, exc)
            except Exception as exc:
                logger.debug("Embedding provider '%s' failed: %s", model_name, exc)

        raise LLMUnavailableError(
            "No embedding provider available. Check llm_config.yaml embeddings section. "
            "For deterministic-only environments, set ICDEV_NO_LLM=true and use BM25/keyword search.",
            function="embeddings",
            chain=chain,
            no_llm_mode=False,
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
