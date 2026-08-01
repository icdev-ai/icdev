# [TEMPLATE: CUI // SP-CTI]
"""Config-driven LLM router.

Reads args/llm_config.yaml and resolves each ICDEV™ function to a
provider + model via fallback chain. Probes provider availability
and caches results.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import copy
import json
import os
import re
import time
from datetime import datetime, timezone
from tools.db.storage import get_connection
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tools.config.core_profile import profile_default

try:
    import yaml
except ImportError:
    yaml = None

from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse, EmbeddingProvider

try:
    from tools.llm.response_cache import LLMResponseCache, canonical_key
except ImportError:
    LLMResponseCache = None
    canonical_key = None

logger = get_logger("icdev.llm.router")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Resolved centrally rather than as BASE_DIR/"args"/... — this module exists twice
# (tools/llm and icdev/tools/llm) and the naive expression made each copy read a
# different config file. See tools/llm/config_path.py.
try:
    from tools.llm.config_path import resolve_llm_config_path
except ImportError:  # packaged-only install
    from icdev.tools.llm.config_path import resolve_llm_config_path

# Constrained-network mode: single-flight + inter-call pause, and a rotating
# egress proxy. Both are OFF/no-op unless configured. See the modules' docs.
try:
    from tools.llm.rate_gate import rate_gate, resolve_lease_config, resolve_rate_limit
    from tools.llm.proxy_resolver import apply_llm_proxy, resolve_llm_proxy
except ImportError:  # packaged-only install
    from icdev.tools.llm.rate_gate import rate_gate, resolve_lease_config, resolve_rate_limit
    from icdev.tools.llm.proxy_resolver import apply_llm_proxy, resolve_llm_proxy

DEFAULT_CONFIG_PATH = resolve_llm_config_path()


class CrossGraderViolation(RuntimeError):
    """Raised when no provider is available that isn't excluded by cross-grader constraint.

    The cross-grader constraint prevents the same LLM that ran an agent session
    from grading its own output.  Pass ``exclude_model_ids`` to
    :meth:`LLMRouter.invoke` to enforce it; this exception fires when every
    chain entry is in the exclusion list.
    """


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


def _resolve_redaction_fail_closed(llm_redaction_cfg: dict) -> bool:
    """Resolve ``redaction.fail_closed`` from the file that actually documents it.

    THE BUG THIS FIXES (prem-p0-03): the router read this flag from
    ``self._config`` — i.e. ``args/llm_config.yaml`` — whose ``redaction:`` block
    has no ``fail_closed`` key at all. The flag is documented, commented, and set
    in ``args/redaction_config.yaml``, right next to ``mask_at_ingestion``, which
    IS read from there (by the RAG ingestion manager).

    So ``fail_closed`` resolved to False unconditionally, ``RedactionUnavailableError``
    could never fire, and an operator who set ``fail_closed: true`` in the file that
    documents it got NO protection and no warning. A security control that silently
    does nothing is worse than one that is absent: it is believed in.

    Precedence: an explicit key in llm_config.yaml wins (so a deployment can pin it
    next to the routing it belongs to), otherwise the canonical redaction_config.yaml.
    """
    if "fail_closed" in (llm_redaction_cfg or {}):
        return bool(llm_redaction_cfg["fail_closed"])
    try:
        import yaml  # noqa: PLC0415

        path = Path(__file__).resolve().parents[2] / "args" / "redaction_config.yaml"
        with open(path, encoding="utf-8") as fh:
            return bool((yaml.safe_load(fh) or {}).get("fail_closed", False))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("could not read redaction_config.yaml fail_closed: %s", exc)
        return False


def _chain_is_local_only(chain, models: dict, providers: dict) -> bool:
    """Fail-closed wrapper around the ONE locality definition.

    If the locality check cannot even be imported we cannot PROVE the chain stays
    on this machine, so we answer False (not local). Answering True would skip
    redaction on a guarantee we never verified.

    Takes config model NAMES (``qwen3-local``), not provider-side model ids
    (``qwen3:latest``) — see _provider_is_local_only for the reason that matters.
    """
    try:
        from tools.llm.cli_bridge.activate import is_local_only_chain
    except Exception:  # pragma: no cover - defensive
        return False
    return is_local_only_chain(chain, models or {}, providers or {})


def _provider_is_local_only(provider_name: str, providers: dict) -> bool:
    """Fail-closed: does *provider_name* never leave the machine?

    Used at the provider call, where we hold a ``model_cfg`` (which names its
    provider) but only the provider-side ``model_id``. Deciding locality from the
    id would silently always be False — the models map is keyed by config name.
    """
    try:
        from tools.llm.cli_bridge.activate import _is_local_only_provider
    except Exception:  # pragma: no cover - defensive
        return False
    return _is_local_only_provider(provider_name or "", providers or {})


class ForceLocalViolation(RuntimeError):
    """Raised when CUI would egress to a non-local provider.

    The fail-closed rung of the CUI egress guarantee. Raised when the routing
    policy resolved to LOCAL — because the install is air-gapped, because the
    content is classified (declared or derived), or because the function is
    declared ``force_local`` — and the model about to be invoked does not run on
    a local-only provider.

    Refusing is the correct behavior. Falling through to cloud is the bug this
    exception exists to prevent.

    Attributes:
        function: ICDEV™ function whose egress was blocked (may be "" for
            content-only paths such as ChainOrchestrator's ``_invoke_model_direct``).
        chain: the chain under consideration, when known.
        model: the specific model that would have egressed.
        rule: which policy rung fired (see tools/llm/routing_policy.py).
    """

    def __init__(self, message: str, *, function: str = "", chain=None, model: str = "", rule: str = ""):
        super().__init__(message)
        self.function = function
        self.chain = list(chain or [])
        self.model = model
        self.rule = rule


class RedactionUnavailableError(RuntimeError):
    """Raised (only when ``redaction.fail_closed`` is enabled) when the PII/CUI
    sanitizer that MUST run before an LLM call is missing or errors — so raw
    sensitive text is never sent unredacted (trust-mask-01).

    Fail-open (the default) logs and proceeds; fail-closed raises this to abort
    the LLM call. Legitimate skips (redaction disabled, excluded function,
    local-only routing) never raise — they are not failures.

    Attributes:
        function: ICDEV™ function whose egress was blocked.
    """

    def __init__(self, message: str, *, function: str = ""):
        super().__init__(message)
        self.function = function


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


# ---------------------------------------------------------------------------
# Retry / graceful-degradation helpers (module-level so tests can import them)
# ---------------------------------------------------------------------------

_RETRY_DELAYS: list = [5, 15]
"""Default sleep seconds between retry attempts (attempt 1→2, 2→3)."""


def _is_transient(exc: Exception) -> bool:
    """Return True when *exc* looks like a temporary infrastructure fault worth retrying.

    Non-retryable types (CrossGraderViolation, ValueError, PermissionError) are
    excluded unconditionally; everything else is classified by message keywords so
    the router backs off on timeouts, rate limits, and 5xx responses.
    """
    if isinstance(exc, (CrossGraderViolation, ValueError, PermissionError)):
        return False
    msg = str(exc).lower()
    return any(
        s in msg
        for s in ("timeout", "rate limit", "503", "502", "temporarily", "overloaded", "connection")
    )


class LLMRouter:
    """Config-driven router that maps ICDEV™ functions to LLM providers.

    Walks fallback chains, probes availability, and returns the first
    responsive provider + model pair.
    """

    # Runtime dual-model toggle (None = use config/env, True/False = explicit)
    _dual_model_runtime: Optional[bool] = None

    # RL router singleton (shared across all LLMRouter instances in process)
    _rl_router_instance = None

    # Response cache singleton (D-CACHE-1)
    _response_cache_instance: Optional["LLMResponseCache"] = None

    # Degraded tier2 models — circuit-breaker for rate-limited models (D-AUTO-DEGRADE)
    _degraded_tier2_models: set = set()
    _degraded_tier2_probed_at: Dict[str, float] = {}
    _DEGRADATION_PROBE_INTERVAL_SECONDS: float = 300.0  # 5 minutes

    # Embedding providers that failed their availability probe — the same
    # circuit-breaker idea applied to `embeddings.default_chain`.
    #
    # Without this, a chain like [openai-embed, gemini-embed, nomic-embed-local]
    # re-probes BOTH cloud providers on every cold process before reaching the
    # local one. Measured on this machine with an invalid OpenAI key: ~12s of
    # failing round-trips ahead of a 0.06s local embed, paid again by every
    # dashboard restart, CLI invocation and worker process.
    #
    # Persisted like _degraded_tier2_* so the knowledge survives process exit;
    # a short TTL means a repaired credential recovers on its own.
    _embedding_unavailable_at: Dict[str, float] = {}
    _EMBEDDING_UNAVAILABLE_TTL_SECONDS: float = 900.0  # 15 minutes

    def __init__(self, config_path=None):
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config: Dict = {}
        self._providers: Dict[str, LLMProvider] = {}
        self._embedding_providers: Dict[str, EmbeddingProvider] = {}
        self._availability_cache: Dict[str, bool] = {}
        self._availability_cache_time: float = 0.0
        self._cache_ttl: float = 1800.0

        self._load_config()
        self._maybe_activate_cli_bridge()
        self._load_degraded_tier2_state()
        self._load_embedding_unavailable()

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
            self._register_discovered_models()
            self._apply_profile_defaults()
        except Exception as exc:
            logger.error("Failed to load LLM config: %s", exc)
            self._config = {}

    def _maybe_activate_cli_bridge(self):
        """Prepend the local Claude CLI provider to routing chains when enabled.

        Auto-on when air-gapped or no cloud API key is configured; gated by the
        ``ICDEV_CLI_BRIDGE`` env var (false = hard-disable, true = force-enable).
        Idempotent and failure-tolerant — never disrupts routing if anything
        goes wrong (e.g. cli_bridge package missing).
        """
        try:
            from tools.llm.cli_bridge.activate import maybe_activate

            self._config = maybe_activate(self._config)
        except Exception as exc:
            logger.debug("CLI bridge hook skipped: %s", exc)

    # -------------------------------------------------------------------
    # Tier2 auto-degradation helpers (D-AUTO-DEGRADE)
    # -------------------------------------------------------------------
    def _degrade_tier2_model(self, model_name: str, resume_at: float = 0.0) -> None:
        """Mark a tier2 model as degraded and persist the state.

        Args:
            model_name: Model to degrade.
            resume_at: Optional Unix timestamp when the model is expected to recover
                (parsed from provider error message). If 0, default probe interval is used.
        """
        LLMRouter._degraded_tier2_models.add(model_name)
        LLMRouter._degraded_tier2_probed_at[model_name] = resume_at if resume_at else time.time()
        logger.warning(
            "Tier2 model degraded: %s (rate limit detected, resume_at=%s)",
            model_name,
            datetime.fromtimestamp(resume_at, tz=timezone.utc).isoformat() if resume_at else "default",
        )
        self._persist_degraded_tier2_state()

    def _recover_tier2_model(self, model_name: str) -> None:
        """Clear degradation for a recovered tier2 model."""
        LLMRouter._degraded_tier2_models.discard(model_name)
        LLMRouter._degraded_tier2_probed_at.pop(model_name, None)
        logger.info("Tier2 model recovered: %s", model_name)
        self._persist_degraded_tier2_state()

    def _is_tier2_degraded(self, model_name: str) -> bool:
        """Check if a tier2 model is currently degraded."""
        return model_name in LLMRouter._degraded_tier2_models

    def _should_probe_degraded(self, model_name: str) -> bool:
        """Check if enough time has passed since last probe (or resume_at) to try again."""
        last_probe = LLMRouter._degraded_tier2_probed_at.get(model_name, 0.0)
        # If resume_at was parsed from error message, use it directly
        if last_probe > time.time():
            return False
        return (time.time() - last_probe) >= self._DEGRADATION_PROBE_INTERVAL_SECONDS

    def _get_degraded_tier2_fallback(self, cfg: dict) -> str:
        """Return the fallback model to use when tier2 is degraded.

        Prefers the configured tier1_model, falling back to qwen3-local or kimi-cloud.
        """
        tier1 = _expand_env(cfg.get("tier1_model", "qwen3-local"))
        return tier1

    def _probe_degraded_tier2(self, model_name: str) -> bool:
        """Attempt a lightweight probe of a degraded tier2 model.

        Returns True if the model responds successfully, False otherwise.
        """
        LLMRouter._degraded_tier2_probed_at[model_name] = time.time()
        model_cfg = self._get_model_config(model_name)
        if not model_cfg:
            return False
        provider_name = model_cfg.get("provider", "")
        provider = self._get_provider(provider_name)
        if provider is None:
            return False
        model_id = model_cfg.get("model_id", "")
        try:
            # Minimal request — hard 8-second timeout
            resp = self._provider_invoke(
                provider,
                LLMRequest(messages=[{"role": "user", "content": "ping"}]),
                model_id,
                model_cfg,
            )
            return bool(resp.content) or bool(resp.stop_reason)
        except Exception:
            return False

    def _persist_degraded_tier2_state(self) -> None:
        """Persist degraded tier2 state to a lightweight JSON file."""
        try:
            state_path = BASE_DIR / "data" / "llm_degraded_tier2.json"
            state = {
                "degraded": sorted(list(LLMRouter._degraded_tier2_models)),
                "probed_at": {k: v for k, v in LLMRouter._degraded_tier2_probed_at.items()},
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass  # Best-effort persistence

    # -------------------------------------------------------------------
    # Embedding availability circuit-breaker
    # -------------------------------------------------------------------
    _EMBEDDING_STATE_FILENAME = "llm_embedding_unavailable.json"

    @classmethod
    def _embedding_state_path(cls):
        return BASE_DIR / "data" / cls._EMBEDDING_STATE_FILENAME

    @classmethod
    def _load_embedding_unavailable(cls) -> None:
        """Load the persisted set of embedding providers that failed probing."""
        try:
            path = cls._embedding_state_path()
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                loaded = state.get("unavailable_at", {})
                if isinstance(loaded, dict):
                    cls._embedding_unavailable_at = {
                        str(k): float(v) for k, v in loaded.items()
                    }
        except Exception:  # noqa: BLE001 - state is an optimisation, never a gate
            pass

    @classmethod
    def _save_embedding_unavailable(cls) -> None:
        try:
            path = cls._embedding_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"unavailable_at": cls._embedding_unavailable_at}, f, indent=2)
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def _embedding_recently_failed(cls, model_name: str) -> bool:
        """True when ``model_name`` failed a probe inside the TTL.

        Time-boxed rather than permanent: a provider marked dead forever would
        make a fixed API key look like it changed nothing.
        """
        ts = cls._embedding_unavailable_at.get(model_name)
        if ts is None:
            return False
        if (time.time() - ts) >= cls._EMBEDDING_UNAVAILABLE_TTL_SECONDS:
            cls._embedding_unavailable_at.pop(model_name, None)
            return False
        return True

    @classmethod
    def _mark_embedding_unavailable(cls, model_name: str) -> None:
        cls._embedding_unavailable_at[model_name] = time.time()
        cls._save_embedding_unavailable()

    @classmethod
    def reset_embedding_availability(cls) -> None:
        """Forget every recorded embedding failure and re-probe on next use.

        For callers that have just fixed a credential and do not want to wait
        out the TTL (e.g. a settings page that saved a new API key).
        """
        cls._embedding_unavailable_at = {}
        cls._save_embedding_unavailable()

    def _load_degraded_tier2_state(self) -> None:
        """Load degraded tier2 state from persistent JSON file."""
        try:
            state_path = BASE_DIR / "data" / "llm_degraded_tier2.json"
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                LLMRouter._degraded_tier2_models = set(state.get("degraded", []))
                LLMRouter._degraded_tier2_probed_at = state.get("probed_at", {})
                if LLMRouter._degraded_tier2_models:
                    logger.info(
                        "Loaded %d degraded tier2 models from state: %s",
                        len(LLMRouter._degraded_tier2_models),
                        sorted(list(LLMRouter._degraded_tier2_models)),
                    )
        except Exception:
            pass

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

        # LPX (lpx-proxy-03): when the opt-in LLM proxy gateway is enabled,
        # redirect cloud providers' base_url to the proxy and swap in a virtual
        # key. No-op (default) when ICDEV_LLM_PROXY_ENABLED is unset, and never
        # touches ollama/bedrock. Distinct from proxy_resolver's HTTPS_PROXY.
        try:
            from tools.llm.proxy_gateway import apply_gateway_to_provider_cfg

            provider_cfg = apply_gateway_to_provider_cfg(provider_name, provider_cfg)
        except Exception as exc:  # pragma: no cover - defensive import guard
            logger.debug("LLM proxy gateway resolution skipped: %s", exc)

        # LPX (lpx-keys-04): a per-person LOCAL CANVAS COPY must fail CLOSED with a
        # clear message when it has no virtual key — never silently fall back to a
        # real provider key. This runs OUTSIDE the try/except above on purpose so
        # LocalCopyEgressError is NOT swallowed. apply_gateway_to_provider_cfg has
        # already forced api_key_env to the virtual key, so even a swallowed raise
        # cannot leak a real key. No-op unless ICDEV_LLM_LOCAL_COPY is truthy.
        from tools.llm.proxy_gateway import enforce_local_copy_egress

        enforce_local_copy_egress(provider_cfg)

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
                api_key_env = provider_cfg.get("api_key_env", "")
                api_key = _expand_env(provider_cfg.get("api_key", ""))
                if not api_key and api_key_env:
                    api_key = os.getenv(api_key_env, "")
                instance = OllamaProvider(base_url=base_url, api_key=api_key)

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

            elif ptype == "cli":
                from tools.llm.cli_bridge.cli_provider import CLILLMProvider

                instance = CLILLMProvider(
                    cli_binary=_expand_env(provider_cfg.get("cli_binary", "claude")),
                    backend=provider_cfg.get("backend", "auto"),
                    soft_wait_seconds=int(provider_cfg.get("soft_wait_seconds", 60)),
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

    def is_force_local(self, function: str) -> bool:
        """True if *function* is declared ``force_local`` in args/llm_config.yaml.

        Public so callers can honour the declaration without re-parsing the config.
        """
        from tools.llm.routing_policy import is_force_local as _ifl

        return _ifl(function, self._config)

    def _get_chain_for_function(self, function: str) -> list:
        """Get the model chain for a function.

        Applies the request-scoped CLI bridge override (if set) so a per-page
        toggle takes effect at invoke time even though the base config was
        rewritten once at construction. A ``False`` override strips the
        ``claude-cli`` model from the chain so invocation bypasses the bridge.

        Then filters to local models for a ``force_local`` function. This runs
        AFTER the bridge override deliberately: a ``True`` override PREPENDS
        ``claude-cli`` — a cloud egress path — to whatever chain it is handed, so a
        chain that is local-only in YAML can still be fronted by the bridge at
        invoke time. The old per-function pins never closed that.

        This filtering is DEFENCE IN DEPTH, not the guarantee. The guarantee is
        _enforce_routing_policy at the provider call, which also covers the paths
        that never consult a chain at all (two_tier) or rewrite it afterwards (RL
        re-ranking). Filtering here means a force_local call PREFERS its local
        model rather than refusing each cloud model in turn.
        """
        routing = self._config.get("routing", {})
        route = routing.get(function, routing.get("default", {}))
        chain = route.get("chain", [])
        try:
            from tools.llm.cli_bridge.activate import apply_cli_bridge_override

            chain = apply_cli_bridge_override(chain)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("CLI bridge override skipped: %s", exc)
            chain = list(chain)

        if not (route or {}).get("force_local", False):
            return chain

        try:
            from tools.llm.cli_bridge.activate import is_local_only_model
        except ImportError as exc:  # pragma: no cover - defensive
            # Cannot prove locality => cannot permit the call. Refuse, don't guess.
            raise ForceLocalViolation(
                f"Function '{function}' is force_local but the locality check is "
                f"unavailable ({exc}); refusing to route CUI without proof it stays local.",
                function=function,
                chain=chain,
                rule="force_local",
            ) from exc

        models = self._config.get("models", {})
        providers = self._config.get("providers", {})
        local_chain = [m for m in chain if is_local_only_model(m, models, providers)]

        dropped = [m for m in chain if m not in local_chain]
        if dropped:
            # Not debug: a cloud model reaching a CUI function is a security event.
            logger.warning(
                "force_local[%s]: dropped non-local model(s) %s — CUI must not egress",
                function, dropped,
            )

        if not local_chain:
            raise ForceLocalViolation(
                f"Function '{function}' is force_local (handles raw CUI) but no local "
                f"provider remains in its chain {list(chain)!r}. Refusing to route to "
                f"cloud. Start Ollama, or fix the chain in args/llm_config.yaml.",
                function=function,
                chain=chain,
                rule="force_local",
            )
        return local_chain

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

    def _compress_request_context(self, function: str, request: LLMRequest) -> LLMRequest:
        """Apply Headroom-style context compression before the fallback chain (adapt-hd-03).

        Only compresses when compression.enabled=true in llm_config.yaml and
        function is not in compression.exempt_functions. No-op by default.
        """
        try:
            from tools.llm.compression.context_compressor import compress, load_config_from_yaml
            cfg = load_config_from_yaml()
            if not cfg.enabled:
                return request
            if function in cfg.exempt_functions:
                return request
            return compress(request, cfg)
        except Exception:
            return request  # Never block the LLM call on compressor failure

    def _pre_invoke_redaction(self, function: str, request: LLMRequest) -> Optional[str]:
        """Sanitize PII in request messages before sending to any LLM.

        Applies to ALL modules. Checks if the function is in the enforced
        scope and whether routing is local-only (skips if configured).

        Returns session_id for de-anonymization, or None if skipped.

        trust-mask-01: when ``redaction.fail_closed`` is enabled, a sanitizer
        that MUST run but is missing/errors raises RedactionUnavailableError
        (aborting the LLM call) instead of proceeding unredacted. Default is
        fail-open (log + proceed). Legitimate skips below never raise.
        """
        # D-RDT-4: Config toggle — skip redaction if explicitly disabled
        rdcfg = self._config.get("redaction", {})
        fail_closed = _resolve_redaction_fail_closed(rdcfg)
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
            # Sanitizer module unavailable. Fail-open: skip (legacy). Fail-closed:
            # block egress — raw PII/CUI must never leave unredacted.
            if fail_closed:
                raise RedactionUnavailableError(
                    f"Redaction sanitizer unavailable for '{function}' and "
                    f"redaction.fail_closed is enabled — LLM call blocked.",
                    function=function,
                )
            return None

        try:
            # Check if routing is local-only for this function.
            #
            # This used to be an inline `provider == "ollama"` check with an
            # `if self._get_model_config(m)` filter — a SECOND definition of
            # "local", and a fail-OPEN one: a model with no config was silently
            # dropped from the generator rather than counted as non-local, so a
            # chain of only-unresolvable models produced an EMPTY generator,
            # `all([])` returned True, and the sanitizer skipped redaction
            # entirely — sending raw CUI to a model we cannot even resolve.
            #
            # Use the one definition (cli_bridge.activate), which fails closed on
            # an unknown model and on an empty chain.
            chain = self._get_chain_for_function(function)
            is_local = _chain_is_local_only(
                chain, self._config.get("models", {}), self._config.get("providers", {})
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

        except RedactionUnavailableError:
            raise
        except Exception as exc:
            # Sanitization errored mid-flight. Fail-closed blocks egress;
            # fail-open logs and proceeds (legacy default).
            if fail_closed:
                raise RedactionUnavailableError(
                    f"Redaction failed for '{function}' ({exc}) and "
                    f"redaction.fail_closed is enabled — LLM call blocked.",
                    function=function,
                ) from exc
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

    def _invoke_model_direct(
        self, model_name: str, request: LLMRequest, function: str = ""
    ) -> Optional[LLMResponse]:
        """Invoke a specific named model without chain fallback.

        Returns None on any error so callers can fall through to chain.

        ``function`` is threaded through so the routing policy can apply the
        ``force_local`` rung here too. This is the two-tier AND ChainOrchestrator
        (CoT/CoD) path: it carries real content, and before this it carried no
        function name at all, so a force_local function's content could arrive
        here anonymous.
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
            return self._provider_invoke(provider, request, model_id, model_cfg, function=function)
        except (ForceLocalViolation, RedactionUnavailableError):
            # NEVER swallow an egress refusal into a None. The `return None` below
            # means "this model failed, try the next one" — turning a policy
            # refusal into a fallback would walk the chain looking for a model
            # that IS allowed to leak, which is the exact opposite of the intent.
            raise
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
            # D-CACHE-RAG-1: Insert cache breakpoint marker between injected context and
            # the original system prompt so AnthropicLLMProvider can split the system
            # into separate blocks with cache_control breakpoints on the static portion.
            separator = ""
            if request.cache_control == "ephemeral" and (request.system_prompt or "").strip():
                separator = "\n<!-- cache_breakpoint -->\n"
            req = copy.copy(request)
            req.system_prompt = context_block + citation_block + separator + (request.system_prompt or "")
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
                   WHERE function_name = %s AND deactivated_at IS NULL
                   AND (tenant_id = %s OR tenant_id = '')
                   AND (project_id = %s OR project_id = '')
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
            return self._provider_invoke(provider, request, ollama_model_name, model_cfg)
        except Exception as exc:
            logger.warning(
                "Fine-tuned invoke failed for %s: %s",
                ollama_model_name,
                exc,
            )
            return None

    # -------------------------------------------------------------------
    # Response cache helpers (D-CACHE-1)
    # -------------------------------------------------------------------

    def _get_response_cache(self) -> Optional["LLMResponseCache"]:
        """Return the singleton LLMResponseCache (lazy-init)."""
        if LLMResponseCache is None:
            return None
        if self._response_cache_instance is None:
            try:
                self._response_cache_instance = LLMResponseCache()
            except Exception as exc:
                logger.warning("Response cache unavailable: %s", exc)
                return None
        return self._response_cache_instance

    def _cache_lookup(self, function: str, request: LLMRequest, model_id: str) -> Optional[LLMResponse]:
        """Check response cache before invoking a provider.

        Returns cached LLMResponse on hit, None on miss.
        Skips cache for excluded functions or when cache is disabled.
        """
        cache = self._get_response_cache()
        if cache is None:
            return None

        rcfg = self._config.get("response_cache", {})
        if not rcfg.get("enabled", False):
            return None

        excluded = rcfg.get("excluded_functions", [])
        if function in excluded:
            return None

        try:
            key = canonical_key(function, model_id, request)
        except Exception as exc:
            logger.debug("Cache key computation failed: %s", exc)
            return None

        try:
            hit = cache.get(key)
            if hit is not None:
                logger.debug("Cache hit for %s/%s (key=%s...)", function, model_id, key[:16])
            return hit
        except Exception as exc:
            logger.debug("Cache lookup failed (non-blocking): %s", exc)
            return None

    def _cache_store(
        self,
        function: str,
        request: LLMRequest,
        response: LLMResponse,
        model_id: str,
    ) -> None:
        """Store a successful response in the cache.

        Skips if function is excluded, cache disabled, or response indicates error.
        Respects per-function and per-canvas TTL overrides.
        """
        cache = self._get_response_cache()
        if cache is None:
            return

        rcfg = self._config.get("response_cache", {})
        if not rcfg.get("enabled", False):
            return

        excluded = rcfg.get("excluded_functions", [])
        if function in excluded:
            return

        if response.stop_reason and response.stop_reason.lower() in ("error", "tool_use"):
            return

        ttl = rcfg.get("ttl_seconds", 3600)
        per_fn = rcfg.get("per_function", {}).get(function, {})
        if per_fn:
            ttl = per_fn.get("ttl_seconds", ttl)

        canvas_prefix = function.split("_")[0] if "_" in function else ""
        per_canvas = rcfg.get("per_canvas", {}).get(canvas_prefix, {})
        if per_canvas:
            ttl = per_canvas.get("ttl_seconds", ttl)

        try:
            key = canonical_key(function, model_id, request)
            cache.set(key, response, ttl_seconds=ttl, function=function)
            logger.debug("Cache store for %s/%s (ttl=%ds)", function, model_id, ttl)
        except Exception as exc:
            logger.debug("Cache store failed (non-blocking): %s", exc)

    def _apply_context_cache(self, function: str, request: LLMRequest) -> None:
        """Set request.cache_control for functions/canvases configured for context caching.

        Context caching (provider-level KV prefix reuse) is additive to response caching.
        """
        rcfg = self._config.get("response_cache", {})
        if not rcfg.get("enabled", False):
            return

        canvas_prefix = function.split("_")[0] if "_" in function else ""
        per_canvas = rcfg.get("per_canvas", {}).get(canvas_prefix, {})
        if per_canvas.get("context_cache", False):
            request.cache_control = "ephemeral"
            return

        per_fn = rcfg.get("per_function", {}).get(function, {})
        if per_fn.get("context_cache", False):
            request.cache_control = "ephemeral"

    def _maybe_invoke_two_tier(self, function: str, request: LLMRequest) -> Optional[LLMResponse]:
        """Apply two-tier routing if function is configured for it.

        Returns LLMResponse if two-tier handled the call, else None
        (caller falls through to normal chain-based routing).

        Three paths:
          planner_functions  → Claude directly (no qwen3 pre-step)
          worker_functions   → qwen3 compact draft → Claude review
          scanner_functions  → qwen3 only (no review)

        D-AUTO-DEGRADE: When tier2 (Claude) hits rate limits, it is degraded
        and the fallback tier1 model is used instead. Recovery probes run
        every 5 minutes to detect when Anthropic is available again.
        """
        cfg = self._config.get("two_tier", {})
        # Allow env var override: LLM_TWO_TIER_ENABLED=false disables two-tier
        env_enabled = os.environ.get("LLM_TWO_TIER_ENABLED", "").lower()
        if env_enabled in ("false", "0", "no"):
            return None
        if not cfg.get("enabled", False) and env_enabled not in ("true", "1", "yes"):
            return None

        # Tool-use / agentic turns bypass two-tier. Two-tier (draft→review) is a
        # text-generation pattern: the review step would strip the model's
        # tool_calls and return refined prose, which breaks any agent loop that
        # drives on `LLMResponse.tool_calls`. When the request carries tools,
        # fall through to the normal chain so a tool-capable model is invoked
        # directly and its tool_calls are preserved.
        if getattr(request, "tools", None):
            return None

        tier1 = _expand_env(cfg.get("tier1_model", "qwen3-local"))
        tier2 = _expand_env(cfg.get("tier2_model", "claude-sonnet"))
        planners = cfg.get("planner_functions", [])
        workers = cfg.get("worker_functions", [])
        scanners = cfg.get("scanner_functions", [])

        # D-AUTO-DEGRADE: If tier2 is degraded, use tier1 as fallback
        effective_tier2 = tier2
        if self._is_tier2_degraded(tier2):
            if self._should_probe_degraded(tier2):
                if self._probe_degraded_tier2(tier2):
                    self._recover_tier2_model(tier2)
                else:
                    effective_tier2 = self._get_degraded_tier2_fallback(cfg)
                    logger.warning(
                        "Two-tier: tier2 %s is degraded, falling back to %s for %s",
                        tier2,
                        effective_tier2,
                        function,
                    )
            else:
                effective_tier2 = self._get_degraded_tier2_fallback(cfg)
                logger.debug(
                    "Two-tier: tier2 %s still degraded, using %s for %s",
                    tier2,
                    effective_tier2,
                    function,
                )

        # Dual-model mode: swap tier1 for smaller model to fit 2 models in VRAM
        if self.is_dual_model_active(cfg):
            dm = cfg.get("dual_model", {})
            override_tier1 = dm.get("tier1_override")
            if override_tier1:
                tier1 = override_tier1
                logger.debug("Dual-model active: tier1 swapped to %s", tier1)

        if function in planners:
            # Claude plans directly (or degraded fallback)
            logger.debug("Two-tier: %s → planner (%s direct)", function, effective_tier2)
            try:
                result = self._invoke_model_direct(effective_tier2, request)
            except Exception as exc:
                # D-AUTO-DEGRADE: Detect rate limit during invocation
                if self._is_rate_limit_error(exc):
                    resume_at = self._parse_reset_time_from_error(exc)
                    self._degrade_tier2_model(effective_tier2, resume_at=resume_at)
                    fallback = self._get_degraded_tier2_fallback(cfg)
                    logger.warning(
                        "Two-tier: planner %s rate-limited for %s, falling back to %s (resume_at=%s)",
                        effective_tier2,
                        function,
                        fallback,
                        datetime.fromtimestamp(resume_at, tz=timezone.utc).isoformat() if resume_at else "default",
                    )
                    result = self._invoke_model_direct(fallback, request)
                else:
                    raise
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
                    "Two-tier: %s → worker (fine-tuned %s draft → %s review)",
                    function,
                    ft_override,
                    effective_tier2,
                )
                draft = self._invoke_finetuned_model(
                    ft_override,
                    self._draft_request(augmented),
                )
            else:
                # Default: qwen3 drafts
                logger.debug("Two-tier: %s → worker (qwen3 draft → %s review)", function, effective_tier2)
                draft = self._invoke_model_direct(tier1, self._draft_request(augmented))

            if draft is not None:
                review_req = self._review_request(request, draft, function)
                try:
                    reviewed = self._invoke_model_direct(effective_tier2, review_req)
                except Exception as exc:
                    # D-AUTO-DEGRADE: Detect rate limit during review
                    if self._is_rate_limit_error(exc):
                        resume_at = self._parse_reset_time_from_error(exc)
                        self._degrade_tier2_model(effective_tier2, resume_at=resume_at)
                        logger.warning(
                            "Two-tier: review %s rate-limited for %s, returning draft (resume_at=%s)",
                            effective_tier2,
                            function,
                            datetime.fromtimestamp(resume_at, tz=timezone.utc).isoformat() if resume_at else "default",
                        )
                        return draft
                    else:
                        raise
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

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """Check if an exception is a rate-limit error."""
        exc_str = str(exc).lower()
        if "llmratelimit" in type(exc).__name__.lower() or "rate limit" in exc_str:
            return True
        if any(pattern in exc_str for pattern in (
            "429", "too many requests", "token limit", "quota exceeded",
            "usage limit", "capacity", "please try again", "exceeded",
        )):
            return True
        return False

    @staticmethod
    def _parse_reset_time_from_error(exc: Exception) -> float:
        """Parse a reset/resume time from an Anthropic rate-limit error message.

        Returns Unix timestamp (float) if a reset time is found, otherwise 0.0.
        """
        import re
        from datetime import datetime, timezone

        text = str(exc)
        now = datetime.now(timezone.utc)

        # Pattern 1: "resets 7am (America/New_York)" or "resets at 7am"
        m = re.search(r'resets\s+(?:at\s+)?(\d{1,2}:\d{2}\s*(?:am|pm))', text, re.IGNORECASE)
        if m:
            time_str = m.group(1).strip()
            # Try to parse with timezone context — assume today, use UTC if no tz
            try:
                # Use dateparser if available; otherwise simple parse
                try:
                    import dateparser
                    dt = dateparser.parse(time_str, settings={'RELATIVE_BASE': now.replace(tzinfo=None)})
                    if dt:
                        return dt.replace(tzinfo=timezone.utc).timestamp()
                except ImportError:
                    pass
                # Simple fallback: parse "7:00am" as today's time in UTC
                fmt = "%I:%M%p" if ":" in time_str else "%I%p"
                parsed = datetime.strptime(time_str.replace(":", "").replace(" ", ""), fmt)
                resume = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                if resume <= now:
                    resume = resume.replace(day=resume.day + 1)  # Next day if already passed
                return resume.timestamp()
            except Exception:
                pass

        # Pattern 2: "try again in 5 minutes" or "retry after 5 minutes"
        m = re.search(r'(?:try again|retry after|wait)\s+(?:in\s+)?(\d+)\s*minute', text, re.IGNORECASE)
        if m:
            minutes = int(m.group(1))
            return (now.replace(minute=now.minute + minutes)).timestamp()

        # Pattern 3: ISO timestamp in error body (some APIs embed JSON)
        m = re.search(r'"retry_after"\s*:\s*"([^"]+)"', text)
        if m:
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(m.group(1).replace("Z", "+00:00"))
                return dt.timestamp()
            except Exception:
                pass

        return 0.0

    def _invoke_chain(
        self,
        function: str,
        request: LLMRequest,
        chain: list,
        exclude_model_ids=None,
        _redaction_session=None,
    ) -> LLMResponse:
        """Walk *chain* once, trying each provider in order.

        Returns the first successful LLMResponse.
        Raises CrossGraderViolation when all entries are excluded, or
        LLMUnavailableError when all entries fail.  Callers that want
        exponential-backoff retry should catch LLMUnavailableError and call
        again after checking _is_transient().
        """
        last_error = None

        # D286: Create trace span for LLM invocation
        try:
            from icdev.tools.observability import get_tracer

            tracer = get_tracer()
        except ImportError:
            tracer = None

        # Cross-grader: track how many entries are excluded so we can raise correctly.
        _excluded_count = 0
        _chain_total = len(chain)
        # First routing-policy refusal seen while walking the chain. Kept so an
        # exhausted chain reports "content cannot egress" rather than a bogus
        # "all providers failed".
        _policy_refusal = None

        for model_name in chain:
            model_cfg = self._get_model_config(model_name)
            if not model_cfg:
                continue
            provider_name = model_cfg.get("provider", "")
            provider = self._get_provider(provider_name)
            if provider is None:
                continue
            model_id = model_cfg.get("model_id", "")

            # Cross-grader enforcement: skip models in the exclusion list.
            if exclude_model_ids and model_id in exclude_model_ids:
                logger.debug(
                    "router: skipping model %r (function=%r) — excluded by cross-grader constraint",
                    model_id, function,
                )
                _excluded_count += 1
                continue

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
                _start = time.time()
                # Stamp route-level config onto request so providers can read flags like disable_thinking
                route_cfg = self._config.get("routing", {}).get(function, {})
                request._route_config = route_cfg
                response = self._provider_invoke(provider, request, model_id, model_cfg, function=function)
                _latency = int((time.time() - _start) * 1000)

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

                # Record module-level budget usage for generative_intelligence
                try:
                    from tools.budget.module_budget_tracker import (
                        PREDICTIVE_ANALYSIS_FUNCTIONS,
                        record_module_usage,
                    )

                    _resp_cost = getattr(response, "cost_usd", 0.0) or 0.0
                    _resp_tokens = (getattr(response, "input_tokens", 0) or 0) + (
                        getattr(response, "output_tokens", 0) or 0
                    )

                    record_module_usage(
                        "generative_intelligence",
                        cost_usd=_resp_cost,
                        tokens=_resp_tokens,
                        function=function,
                        project_id=getattr(request, "project_id", None),
                        model_id=getattr(response, "model_id", model_id),
                    )

                    # Also record predictive_analysis usage for simulation functions
                    if function in PREDICTIVE_ANALYSIS_FUNCTIONS:
                        record_module_usage(
                            "predictive_analysis",
                            cost_usd=_resp_cost,
                            tokens=_resp_tokens,
                            function=function,
                            project_id=getattr(request, "project_id", None),
                            model_id=getattr(response, "model_id", model_id),
                        )
                except Exception as exc:
                    # Best-effort — never block a completed LLM call on budget
                    # bookkeeping. But log it: a bare `pass` here hid a schema
                    # mismatch that made every insert fail, so
                    # module_budget_usage stayed empty and budget enforcement
                    # silently read a table that could never fill.
                    logger.warning("Module budget recording failed: %s", exc)

                # D-CACHE-5: Store successful response in cache
                self._cache_store(function, request, response, model_id)

                # D-RDT-2: Post-invoke de-anonymization — restore originals
                response = self._post_invoke_deanonymize(response, _redaction_session)

                # RL: record success so this model's Q-value improves
                self._get_rl_router().record_outcome(function, model_name, success=True, latency_ms=_latency)

                return response
            except ForceLocalViolation as exc:
                # A policy refusal is NOT a provider failure. Keep walking the chain
                # so a LOCAL model further down can serve the call — that is the
                # graceful path, and it is why chain filtering alone is not the
                # guarantee. But remember the refusal: if the chain runs out, the
                # operator must be told "content cannot egress", not the misleading
                # "all providers failed".
                logger.warning(
                    "routing_policy: skipping %s (%s) for %s — %s",
                    provider_name, model_id, function, exc,
                )
                _policy_refusal = _policy_refusal or exc
                # Deliberately NOT marked unavailable and NOT penalised in RL: the
                # model is fine, it is simply not allowed to see THIS content.
                continue
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

        # The chain is exhausted. If ANY entry was refused by the routing policy,
        # that is the real story — the content was not allowed to leave and no local
        # model was available to serve it. Report THAT, not "all providers failed",
        # which would send an operator hunting for a network fault that isn't there.
        if _policy_refusal is not None:
            raise _policy_refusal

        # If every chain entry was excluded, raise CrossGraderViolation instead of
        # LLMUnavailableError so callers can distinguish the two failure modes.
        if exclude_model_ids and _excluded_count > 0 and last_error is None:
            raise CrossGraderViolation(
                "Cross-grader violation: all {} model(s) in the '{}' chain are "
                "excluded by cross-grader constraint (exclude_model_ids={!r}). "
                "Add a different-tier model to the '{}' routing chain in "
                "args/llm_config.yaml.".format(_chain_total, function, exclude_model_ids, function)
            )

        raise LLMUnavailableError(
            "All providers in chain {} failed for function '{}'. Last error: {}".format(chain, function, last_error),
            function=function,
            chain=chain,
            no_llm_mode=False,
        )

    def _apply_network_guard(self, provider) -> None:
        """Apply the current egress proxy before a provider call.

        Resolves the (possibly rotating) proxy fresh each call. When it changed,
        invalidate the provider's cached SDK client so it rebuilds against the
        new proxy (``requests``-based providers pick up env per request). No-op
        and zero-overhead unless a proxy source is configured.
        """
        try:
            proxy = resolve_llm_proxy(self._config)
            if apply_llm_proxy(proxy) and hasattr(provider, "reset_client"):
                provider.reset_client()
        except Exception as exc:  # never let proxy plumbing crash an LLM call
            logger.debug("network guard (proxy) skipped: %s", exc)

    def _enforce_routing_policy(self, function, request, model_id, model_cfg=None) -> None:
        """Refuse the call if policy says LOCAL and this model is not local.

        THE egress guarantee (prem-p0-01). It lives here, at the provider call,
        rather than in chain selection, because chain selection is not the last
        word: ``two_tier`` routing is applied BEFORE the chain is resolved, RL
        re-ranking rewrites the chain afterwards, and the CLI-bridge invoke-time
        override PREPENDS a cloud model to it. Every one of those paths still
        funnels through here.

        Filtering the chain (``_get_chain_for_function``) is defence in depth — it
        makes us *prefer* a local model. This is what makes cloud egress
        impossible.

        Locality is decided from ``model_cfg``'s PROVIDER, not from ``model_id``.
        They are different namespaces and conflating them is a live bug: ``model_id``
        is the provider-side id (``qwen3:latest``) while the models map is keyed by
        the config NAME (``qwen3-local``). Looking up the id finds nothing, and
        "unknown model" fails closed — so a perfectly good LOCAL model gets refused
        and the call dies. Fails safe, but still broken.
        """
        # CUI egress gate (lpx-egress-02): the shared LLM proxy is a NEW cloud
        # egress path. Independently of the general routing-policy threshold,
        # classified/controlled content must never SILENTLY traverse the proxy to
        # a cloud provider. This check is invoke-time (it reads the live request's
        # classification, unavailable at cached-provider construction) and only
        # fires when the proxy WOULD carry this provider — otherwise a no-op. A
        # refusal is raised as ForceLocalViolation so the chain falls back to a
        # local model exactly like any other egress refusal.
        # proxy_gateway is stdlib-only and already imported at provider
        # construction; calling it here cannot introduce a new failure mode. It is
        # deliberately NOT wrapped in a swallow-all except — a gate that fails open
        # is not a gate. Any error propagates as a call failure (fail closed).
        from tools.llm.proxy_gateway import proxy_egress_classification_block

        providers = self._config.get("providers", {})
        provider_name = (model_cfg or {}).get("provider", "")
        provider_cfg = providers.get(provider_name, {}) if provider_name else {}
        _proxy_block = proxy_egress_classification_block(
            provider_cfg, getattr(request, "classification", None)
        )
        if _proxy_block:
            logger.warning(
                "proxy_egress[%s]: REFUSED model '%s' — %s",
                function or "<no function>", model_id, _proxy_block,
            )
            raise ForceLocalViolation(
                f"Refusing to send content to '{model_id}' via the LLM proxy: {_proxy_block}",
                function=function or "",
                model=model_id,
                rule="proxy_cui_egress",
            )

        from tools.llm.routing_policy import resolve as _resolve_policy

        decision = _resolve_policy(function, request, self._config)
        if not decision.local_only:
            return

        if _provider_is_local_only(provider_name, self._config.get("providers", {})):
            return

        # Not debug. A cloud model reaching classified content is a security event.
        logger.warning(
            "routing_policy[%s]: REFUSED model '%s' — %s",
            function or "<no function>", model_id, decision.reason,
        )
        raise ForceLocalViolation(
            f"Refusing to send content to non-local model '{model_id}': {decision.reason} "
            f"(rule: {decision.rule}). Start a local provider (Ollama), or correct the "
            f"chain in args/llm_config.yaml.",
            function=function or "",
            model=model_id,
            rule=decision.rule,
        )

    def _provider_invoke(self, provider, request, model_id, model_cfg, function: str = ""):
        """Single choke point for every non-streaming provider call.

        ALL text and vision generation funnels through here (vision requests
        carry image content in the same ``request``). Honors constrained-network
        mode: rotating-proxy application + the rate-limit gate (serialize to
        ``max_parallel`` + randomized inter-call pause). Both are no-ops unless
        configured, so default behavior is unchanged.

        Enforces the routing policy before anything leaves the process.
        """
        self._enforce_routing_policy(function, request, model_id, model_cfg)
        self._apply_network_guard(provider)
        mp, pmin, pmax = resolve_rate_limit(self._config)
        lease_backend, lease_name, lease_timeout = resolve_lease_config(self._config)
        with rate_gate(
            mp, pmin, pmax,
            lease_backend=lease_backend, lease_name=lease_name, lease_timeout=lease_timeout,
        ):
            return provider.invoke(request, model_id, model_cfg)

    def _provider_invoke_streaming(self, provider, request, model_id, model_cfg, function: str = ""):
        """Streaming counterpart of :meth:`_provider_invoke`.

        Holds the rate-limit slot across the whole stream (releasing, with the
        inter-call pause, once the generator is exhausted) so a streamed call
        still counts as one in-flight LLM call.

        Enforces the routing policy too. Streaming is a SEPARATE chokepoint from
        _provider_invoke — guarding only the non-streaming one would leave a
        fully-open cloud egress path for exactly the same content.
        """
        self._enforce_routing_policy(function, request, model_id, model_cfg)
        self._apply_network_guard(provider)
        mp, pmin, pmax = resolve_rate_limit(self._config)
        if mp <= 0:
            return provider.invoke_streaming(request, model_id, model_cfg)
        lease_backend, lease_name, lease_timeout = resolve_lease_config(self._config)

        def _gated_stream():
            with rate_gate(
                mp, pmin, pmax,
                lease_backend=lease_backend, lease_name=lease_name, lease_timeout=lease_timeout,
            ):
                yield from provider.invoke_streaming(request, model_id, model_cfg)

        return _gated_stream()

    def invoke(
        self,
        function: str,
        request: LLMRequest,
        *,
        exclude_model_ids: list[str] | None = None,
        max_retries: int = 2,
    ) -> LLMResponse:
        """Resolve provider for function and invoke with fallback.

        Walks the full fallback chain: if the first provider fails at
        invocation time (e.g. missing credentials, network error), tries
        the next model in the chain rather than raising immediately.

        Args:
            function: ICDEV™ function name (e.g. 'code_generation', 'nlq_sql').
            request: Vendor-agnostic LLM request.
            exclude_model_ids: Optional list of ``model_id`` strings to skip.
                Used by the cross-grader constraint to prevent the same LLM
                that ran an agent session from grading its own output.
                Raises :class:`CrossGraderViolation` if all chain entries
                are excluded.

        Returns:
            LLMResponse.

        Raises:
            LLMUnavailableError: If no provider in the chain can serve the
                request. Subclass of ``RuntimeError`` so existing
                ``except RuntimeError`` blocks remain compatible. Callers
                that need to degrade to deterministic behavior should
                catch ``LLMUnavailableError`` specifically.
            CrossGraderViolation: If ``exclude_model_ids`` is set and every
                chain entry is excluded.
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

        # Module-level budget enforcement for generative_intelligence and predictive_analysis
        try:
            from tools.budget.module_budget_tracker import (
                PREDICTIVE_ANALYSIS_FUNCTIONS,
                check_module_budget,
                ModuleBudgetExceededError,
            )

            _estimated_tokens = sum(
                len(m.get("content", "")) for m in (request.messages or []) if isinstance(m, dict)
            ) // 4  # rough token estimate for pre-check

            mod_budget = check_module_budget(
                "generative_intelligence",
                function=function,
                estimated_cost_usd=0.0,  # actual cost recorded post-invoke
                estimated_tokens=_estimated_tokens,
            )
            if mod_budget["action"] == "block":
                raise ModuleBudgetExceededError("generative_intelligence", mod_budget)
            if mod_budget["action"] == "warn":
                logger.warning("Module budget warning: %s", mod_budget["message"])

            # Also enforce predictive_analysis budget when invoking simulation functions
            if function in PREDICTIVE_ANALYSIS_FUNCTIONS:
                pa_budget = check_module_budget(
                    "predictive_analysis",
                    function=function,
                    estimated_cost_usd=0.0,
                    estimated_tokens=_estimated_tokens,
                )
                if pa_budget["action"] == "block":
                    raise ModuleBudgetExceededError("predictive_analysis", pa_budget)
                if pa_budget["action"] == "warn":
                    logger.warning("Predictive analysis budget warning: %s", pa_budget["message"])
        except ImportError:
            pass
        except ModuleBudgetExceededError:
            raise
        except Exception as exc:
            logger.debug("Module budget check failed (non-blocking): %s", exc)

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

        # D-CACHE-2: Apply context cache hints before routing
        self._apply_context_cache(function, request)

        # Apply configured effort if not set on request
        if not request.effort or request.effort == "medium":
            request.effort = self.get_effort(function)

        # D-CACHE-3: Response cache lookup (before provider selection)
        chain = self._get_chain_for_function(function)
        if chain:
            first_model = chain[0]
            model_cfg_for_key = self._get_model_config(first_model) or {}
            model_id_for_key = model_cfg_for_key.get("model_id", first_model)
            cached = self._cache_lookup(function, request, model_id_for_key)
            if cached is not None:
                return cached

        # adapt-hd-03: Context compression — apply before fallback chain
        request = self._compress_request_context(function, request)

        # Two-tier routing: qwen3 worker → Claude planner/reviewer
        two_tier_result = self._maybe_invoke_two_tier(function, request)
        if two_tier_result is not None:
            # D-CACHE-4: Store two-tier results too
            self._cache_store(function, request, two_tier_result, two_tier_result.model_id)
            return two_tier_result

        # Chain of Thought / Chain of Debate mode switch
        if request.chain_mode == "cot":
            return self.invoke_chain_of_thought(function, request)
        if request.chain_mode == "cod":
            return self.invoke_chain_of_debate(function, request)

        chain = self._get_chain_for_function(function)
        # RL routing: reorder chain by learned Q-values (epsilon-greedy)
        chain = self._get_rl_router().rank_models(function, chain)

        # Graceful degradation: retry the full chain on transient infrastructure failures
        # (timeouts, 503s, rate limits). Non-transient errors (auth, config) raise immediately.
        _router_cfg = self._config.get("router", {})
        _retry_delays = _router_cfg.get("retry_delays_seconds", _RETRY_DELAYS)
        _eff_retries = min(max_retries, _router_cfg.get("max_retries", max_retries))

        for _attempt in range(_eff_retries + 1):
            try:
                return self._invoke_chain(
                    function, request, chain, exclude_model_ids, _redaction_session
                )
            except CrossGraderViolation:
                raise  # non-retryable: cross-grader config issue
            except LLMUnavailableError as _exc:
                if _attempt < _eff_retries and _is_transient(_exc):
                    _delay = (
                        _retry_delays[_attempt]
                        if _attempt < len(_retry_delays)
                        else (_retry_delays[-1] if _retry_delays else 5)
                    )
                    logger.warning(
                        "router: all providers failed (attempt %d/%d), retrying in %ds — %s",
                        _attempt + 1,
                        _eff_retries + 1,
                        _delay,
                        _exc,
                    )
                    time.sleep(_delay)
                    continue
                raise

    def invoke_chain_of_thought(self, function: str, request: LLMRequest) -> LLMResponse:
        """Invoke Chain of Thought via ChainOrchestrator.

        Reads chain_orchestration.cot config and delegates to the orchestrator.
        Returns an LLMResponse with aggregated metadata.
        """
        try:
            from tools.llm.chain_orchestrator import ChainOrchestrator
        except ImportError as exc:
            raise LLMUnavailableError(
                f"ChainOrchestrator not available: {exc}",
                function=function,
                no_llm_mode=False,
            ) from exc

        orchestrator = ChainOrchestrator(router=self)
        result = orchestrator.invoke_chain_of_thought(function, request)

        # Aggregate into LLMResponse
        response = LLMResponse(
            content=result.content,
            model_id=",".join(result.models_used),
            provider="chain_orchestrator",
            input_tokens=result.total_input_tokens,
            output_tokens=result.total_output_tokens,
            duration_ms=result.total_duration_ms,
            stop_reason=result.stop_reason,
            classification=request.classification,
        )
        # Attach chain metadata for downstream consumers
        response.chain_trace_id = result.trace_id  # type: ignore[attr-defined]
        response.chain_mode = result.chain_mode  # type: ignore[attr-defined]
        response.chain_rounds = result.rounds  # type: ignore[attr-defined]
        response.chain_confidence = result.confidence  # type: ignore[attr-defined]

        # Log aggregated telemetry
        try:
            self._log_telemetry(
                function=function,
                request=request,
                response=response,
                model_id=",".join(result.models_used),
                provider_name="chain_orchestrator",
                latency_ms=result.total_duration_ms,
            )
        except Exception:
            pass

        return response

    def invoke_chain_of_debate(self, function: str, request: LLMRequest) -> LLMResponse:
        """Invoke Chain of Debate via ChainOrchestrator.

        Reads chain_orchestration.cod config and delegates to the orchestrator.
        Returns an LLMResponse with aggregated metadata.
        """
        try:
            from tools.llm.chain_orchestrator import ChainOrchestrator
        except ImportError as exc:
            raise LLMUnavailableError(
                f"ChainOrchestrator not available: {exc}",
                function=function,
                no_llm_mode=False,
            ) from exc

        orchestrator = ChainOrchestrator(router=self)
        result = orchestrator.invoke_chain_of_debate(function, request)

        response = LLMResponse(
            content=result.content,
            model_id=",".join(result.models_used),
            provider="chain_orchestrator",
            input_tokens=result.total_input_tokens,
            output_tokens=result.total_output_tokens,
            duration_ms=result.total_duration_ms,
            stop_reason=result.stop_reason,
            classification=request.classification,
        )
        response.chain_trace_id = result.trace_id  # type: ignore[attr-defined]
        response.chain_mode = result.chain_mode  # type: ignore[attr-defined]
        response.chain_rounds = result.rounds  # type: ignore[attr-defined]
        response.chain_confidence = result.confidence  # type: ignore[attr-defined]

        try:
            self._log_telemetry(
                function=function,
                request=request,
                response=response,
                model_id=",".join(result.models_used),
                provider_name="chain_orchestrator",
                latency_ms=result.total_duration_ms,
            )
        except Exception:
            pass

        return response

    def invoke_council(self, function: str, request: LLMRequest) -> LLMResponse:
        """Invoke an LLM Council via ChainOrchestrator.

        Fixed-perspective advisors (Contrarian, First Principles Thinker,
        Expansionist, Outsider, Executor) each respond independently, peer-
        review each other anonymously, and a chairman synthesizes a
        structured verdict (agreement / clashes / blind spots / recommendation
        / next step) as `response.content`. See
        `tools.llm.chain_orchestrator.ChainOrchestrator.invoke_council` for
        the full mechanism. Reads `chain_orchestration.council` config.
        """
        try:
            from tools.llm.chain_orchestrator import ChainOrchestrator
        except ImportError as exc:
            raise LLMUnavailableError(
                f"ChainOrchestrator not available: {exc}",
                function=function,
                no_llm_mode=False,
            ) from exc

        orchestrator = ChainOrchestrator(router=self)
        result = orchestrator.invoke_council(function, request)

        response = LLMResponse(
            content=result.content,
            model_id=",".join(result.models_used),
            provider="chain_orchestrator",
            input_tokens=result.total_input_tokens,
            output_tokens=result.total_output_tokens,
            duration_ms=result.total_duration_ms,
            stop_reason=result.stop_reason,
            classification=request.classification,
        )
        response.chain_trace_id = result.trace_id  # type: ignore[attr-defined]
        response.chain_mode = result.chain_mode  # type: ignore[attr-defined]
        response.chain_rounds = result.rounds  # type: ignore[attr-defined]
        response.chain_confidence = result.confidence  # type: ignore[attr-defined]

        try:
            self._log_telemetry(
                function=function,
                request=request,
                response=response,
                model_id=",".join(result.models_used),
                provider_name="chain_orchestrator",
                latency_ms=result.total_duration_ms,
            )
        except Exception:
            pass

        return response

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
        # See the non-streaming chain walk: a routing-policy refusal is remembered
        # so an exhausted chain reports "content cannot egress" rather than a
        # misleading "all streaming providers failed".
        _policy_refusal = None

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
                return self._provider_invoke_streaming(provider, request, model_id, model_cfg, function=function)
            except ForceLocalViolation as exc:
                # Same contract as the non-streaming chain walk: a policy refusal is
                # not a provider failure. Skip this model so a LOCAL one further down
                # can serve the stream, but remember it — an exhausted chain must
                # report "content cannot egress", not "all streaming providers failed".
                logger.warning(
                    "routing_policy: skipping streaming %s (%s) for %s — %s",
                    provider_name, model_id, function, exc,
                )
                _policy_refusal = _policy_refusal or exc
                continue
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

        # A policy refusal is the real story — report it, not "all providers failed",
        # which would send an operator hunting for a network fault that isn't there.
        if _policy_refusal is not None:
            raise _policy_refusal

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

            # Skip providers that failed a probe recently. This is what makes
            # the local fallback FAST rather than merely correct: without it,
            # every cold process re-pays the full cost of failing past each
            # cloud provider before reaching the local one.
            if self._embedding_recently_failed(model_name):
                logger.debug(
                    "embeddings: skipping %s (probe failed within the last %ds)",
                    model_name, int(self._EMBEDDING_UNAVAILABLE_TTL_SECONDS),
                )
                continue

            mcfg = models.get(model_name, {})
            if not mcfg:
                continue

            provider_name = mcfg.get("provider", "")
            ptype = self._config.get("providers", {}).get(provider_name, {}).get("type", "")

            # LPX (lpx-proxy-03) applies to EMBEDDINGS too.
            #
            # `_get_provider` routes chat traffic through the proxy and swaps in a
            # virtual key, but the embedding chain built its providers straight
            # from raw config — so with ICDEV_LLM_PROXY_ENABLED=true, chat went
            # through the proxy while embeddings still called the real endpoint
            # with the REAL provider key. That defeats the whole point of the
            # gateway ("the real provider key never leaves the proxy") on a path
            # that sends document text off the machine.
            #
            # No-op by default, and `apply_gateway_to_provider_cfg` never touches
            # ollama/bedrock, so the local and CUI paths are unaffected.
            provider_cfg = self._config.get("providers", {}).get(provider_name, {})
            try:
                from tools.llm.proxy_gateway import apply_gateway_to_provider_cfg

                provider_cfg = apply_gateway_to_provider_cfg(provider_name, provider_cfg)
            except Exception as exc:  # pragma: no cover - defensive import guard
                logger.debug("LLM proxy gateway skipped for embeddings: %s", exc)

            try:
                emb = None
                if ptype in ("openai", "openai_compatible"):
                    from tools.llm.embedding_provider import OpenAIEmbeddingProvider

                    pcfg = provider_cfg
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

                    pcfg = provider_cfg
                    region = _expand_env(pcfg.get("region", "us-gov-west-1"))
                    emb = BedrockEmbeddingProvider(
                        region=region,
                        model_id=mcfg.get("model_id", "amazon.titan-embed-text-v2:0"),
                        dims=mcfg.get("dimensions", 1024),
                    )
                elif ptype == "gemini":
                    from tools.llm.embedding_provider import GeminiEmbeddingProvider

                    pcfg = provider_cfg
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

                    pcfg = provider_cfg
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

                    pcfg = provider_cfg
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

                    pcfg = provider_cfg
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

                    pcfg = provider_cfg
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
                # Probed and did not answer — remember it, so the next process
                # falls straight through to the local provider.
                self._mark_embedding_unavailable(model_name)
                logger.info(
                    "Embedding provider %s unavailable — falling through to the "
                    "next entry in embeddings.default_chain", model_name,
                )
            except ImportError as exc:
                self._mark_embedding_unavailable(model_name)
                logger.debug("Embedding provider '%s' not importable: %s", model_name, exc)
            except Exception as exc:
                self._mark_embedding_unavailable(model_name)
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

    # -------------------------------------------------------------------
    # Task-category capability catalog (task_categories: in llm_config.yaml)
    # -------------------------------------------------------------------

    def get_models_for_category(self, category: str) -> List[str]:
        """Return ordered list of logical model names for a task category.

        Reads task_categories.<category>.preferred from llm_config.yaml and
        filters to models that exist in the models: registry (including any
        auto-discovered ones). Returns [] if category is unknown.

        Args:
            category: One of 'coding', 'reasoning', 'writing', 'vision',
                      'summarization', 'long_context', 'structured_output', 'agentic'.
        """
        cats = self._config.get("task_categories", {})
        preferred = cats.get(category, {}).get("preferred", [])
        known = set(self._config.get("models", {}).keys())
        return [m for m in preferred if m in known]

    # -------------------------------------------------------------------
    # CoT/CoD role routing — public, chain-aware, with fallback + RL
    # -------------------------------------------------------------------

    def invoke_for_role(
        self,
        role_key: str,
        function: str,
        request: "LLMRequest",
    ) -> "LLMResponse":
        """Invoke an LLM for a CoT/CoD role via the full fallback chain.

        Unlike _invoke_model_direct(), this method:
        - Checks availability cache per model before attempting
        - Applies RL reranking to the chain
        - Walks the entire fallback chain before giving up
        - Records outcomes for RL learning
        - Logs telemetry under the originating function name
        - Raises LLMUnavailableError (not returns None) on total failure

        Args:
            role_key: Routing chain key from llm_config.yaml routing: section,
                      e.g. 'cot_reasoner', 'cot_critic', 'cod_judge'.
            function: ICDEV™ function name, used for telemetry grouping.
            request:  LLMRequest to invoke.
        """
        chain = self._get_chain_for_function(role_key)
        if not chain:
            raise LLMUnavailableError(
                f"No routing chain defined for role '{role_key}'. "
                f"Add '{role_key}:' under routing: in llm_config.yaml.",
                function=role_key,
                chain=[],
            )

        try:
            chain = self._get_rl_router().rank_models(role_key, chain)
        except Exception:
            pass  # RL ranking is best-effort

        last_error: Optional[Exception] = None

        for model_name in chain:
            model_cfg = self._get_model_config(model_name)
            if not model_cfg:
                continue
            if not self._check_model_available(model_name):
                continue
            provider_name = model_cfg.get("provider", "")
            provider = self._get_provider(provider_name)
            if provider is None:
                continue
            model_id = model_cfg.get("model_id", "")
            try:
                _start = time.time()
                response = self._provider_invoke(provider, request, model_id, model_cfg, function=function)
                _latency = int((time.time() - _start) * 1000)
                try:
                    self._get_rl_router().record_outcome(role_key, model_name, success=True, latency_ms=_latency)
                except Exception:
                    pass
                try:
                    self._log_telemetry(function, request, response, model_id, provider_name, _latency)
                except Exception:
                    pass
                return response
            except Exception as exc:
                logger.warning(
                    "invoke_for_role: %s via %s/%s failed for %s: %s — trying next",
                    role_key, provider_name, model_id, function, exc,
                )
                self._availability_cache[model_name] = False
                try:
                    self._get_rl_router().record_outcome(role_key, model_name, success=False)
                except Exception:
                    pass
                last_error = exc
                continue

        raise LLMUnavailableError(
            f"All models in role chain '{role_key}' failed for function '{function}'. "
            f"Last error: {last_error}",
            function=function,
            chain=chain,
        )

    def get_diverse_models(self, role_key: str, count: int) -> List[str]:
        """Return up to `count` distinct available models, maximizing provider diversity.

        Used by ChainOrchestrator to assign one unique model per CoD debater slot
        so debates involve genuinely different model perspectives.

        First pass: picks models from distinct provider families (ollama, anthropic,
        openai, google, etc.). Second pass: fills remaining slots with any remaining
        available model not already selected. Falls back gracefully when fewer models
        are available than requested.

        Args:
            role_key: Routing chain key (e.g. 'cod_debater_pool').
            count:    Number of distinct models to return (one per debater slot).

        Returns:
            List of logical model names, len <= count. Empty if chain is undefined.
        """
        chain = self._get_chain_for_function(role_key)
        if not chain:
            return []
        try:
            chain = self._get_rl_router().rank_models(role_key, chain)
        except Exception:
            pass

        selected: List[str] = []
        used_providers: set = set()

        # First pass: one model per provider family
        for model_name in chain:
            if len(selected) >= count:
                break
            model_cfg = self._get_model_config(model_name)
            if not model_cfg:
                continue
            if not self._check_model_available(model_name):
                continue
            provider = model_cfg.get("provider", "unknown")
            if provider not in used_providers:
                selected.append(model_name)
                used_providers.add(provider)

        # Second pass: fill remaining slots with any available model not yet chosen
        if len(selected) < count:
            for model_name in chain:
                if len(selected) >= count:
                    break
                if model_name not in selected:
                    model_cfg = self._get_model_config(model_name)
                    if model_cfg and self._check_model_available(model_name):
                        selected.append(model_name)

        return selected

    # -------------------------------------------------------------------
    # Ollama model auto-discovery (startup + periodic refresh)
    # -------------------------------------------------------------------

    def discover_ollama_models(self, force_refresh: bool = False) -> List[str]:
        """Query Ollama /api/tags and return model names not in models: registry.

        Probes each provider in settings.ollama_discovery.probe_providers.
        Results are cached for refresh_interval_seconds. Returns raw Ollama
        model names (e.g. 'phi4-mini:latest') so the caller can register them.

        This is read-only — it does NOT modify llm_config.yaml.

        Args:
            force_refresh: If True, bypass the process-level cache.

        Returns:
            List of raw model names found in Ollama but absent from models: registry.
        """
        disc_cfg = self._config.get("settings", {}).get("ollama_discovery", {})
        if not disc_cfg.get("enabled", True):
            return []

        now = time.time()
        refresh_interval = float(disc_cfg.get("refresh_interval_seconds", 3600))
        cached = getattr(self, "_ollama_discovery_cache", None)
        cached_time = getattr(self, "_ollama_discovery_cache_time", 0.0)
        if not force_refresh and cached is not None and (now - cached_time) < refresh_interval:
            return cached

        known_base_names: set = {
            cfg.get("model_id", "").lower().split(":")[0]
            for cfg in self._config.get("models", {}).values()
            if cfg.get("provider", "").startswith("ollama") or cfg.get("provider", "") == "ollama"
        }

        probe_providers = disc_cfg.get("probe_providers", ["ollama"])
        new_models: List[str] = []

        for pname in probe_providers:
            pcfg = self._config.get("providers", {}).get(pname, {})
            raw_url = pcfg.get("base_url", "http://localhost:11434")
            base_url = _expand_env(raw_url).rstrip("/")
            try:
                import urllib.request as _urllib_req
                import json as _json
                req = _urllib_req.Request(f"{base_url}/api/tags", method="GET")
                api_key_env = pcfg.get("api_key_env", "")
                if api_key_env:
                    ak = os.getenv(api_key_env, "")
                    if ak:
                        req.add_header("Authorization", f"Bearer {ak}")
                with _urllib_req.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read())
                for model in data.get("models", []):
                    raw_name = model.get("name", "").lower().strip()
                    base_name = raw_name.split(":")[0]
                    if base_name not in known_base_names and raw_name not in new_models:
                        new_models.append(raw_name)
            except Exception as exc:
                logger.debug("Ollama discovery probe failed for %s at %s: %s", pname, base_url, exc)

        setattr(self, "_ollama_discovery_cache", new_models)
        setattr(self, "_ollama_discovery_cache_time", now)

        if new_models:
            logger.info("Ollama discovery: %d new model(s) found: %s", len(new_models), new_models)
        return new_models

    def _register_discovered_models(self) -> None:
        """Register newly discovered Ollama models in the in-memory config.

        Called once at the end of _load_config(). Queries /api/tags, finds
        models not in the models: registry, and adds synthetic entries with
        default capabilities. In-memory only — does not write llm_config.yaml.
        """
        disc_cfg = self._config.get("settings", {}).get("ollama_discovery", {})
        if not disc_cfg.get("enabled", True):
            return

        default_caps = disc_cfg.get("default_capabilities", ["summarization", "writing"])
        try:
            new_names = self.discover_ollama_models(force_refresh=True)
        except Exception as exc:
            logger.debug("Ollama model registration skipped: %s", exc)
            return

        for raw_name in new_names:
            # Derive a safe logical name from the raw model name
            logical = raw_name.replace(":", "-").replace("/", "-").replace(".", "-")
            if logical in self._config.get("models", {}):
                continue
            self._config.setdefault("models", {})[logical] = {
                "provider": "ollama",
                "model_id": raw_name,
                "max_output_tokens": 4096,
                "supports_thinking": False,
                "supports_tools": False,
                "supports_structured_output": False,
                "_auto_discovered": True,
                "_default_capabilities": default_caps,
                "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0},
            }
            logger.info(
                "Auto-registered Ollama model: %s → logical '%s' (caps: %s)",
                raw_name, logical, default_caps,
            )

    def _apply_profile_defaults(self) -> None:
        """Adjust the default routing chain from active core profile.

        If ``ICDEV_LLM_DEFAULT_MODEL`` (or the active profile's default_model)
        matches a configured model, prepend it to the default chain so the
        profile's preferred model is tried first. If only a provider is given,
        find the first model for that provider and prepend it.

        This is intentionally limited to the default chain; function-specific
        routes keep their explicit ordering unless no env var overrides them.
        """
        routing = self._config.setdefault("routing", {})
        default_route = routing.setdefault("default", {})
        chain: List[str] = list(default_route.get("chain", []))

        preferred_model = profile_default("ICDEV_LLM_DEFAULT_MODEL", "")
        models = self._config.get("models", {})

        if preferred_model and preferred_model in models:
            target = preferred_model
        else:
            preferred_provider = profile_default("ICDEV_LLM_PROVIDER", "")
            if preferred_provider:
                target = next(
                    (name for name, cfg in models.items() if cfg.get("provider") == preferred_provider),
                    "",
                )
            else:
                target = ""

        if not target or target not in models:
            return

        if chain:
            if target in chain:
                chain.remove(target)
            chain.insert(0, target)
        else:
            chain = [target]
        default_route["chain"] = chain
        logger.info("Core profile promoted '%s' to first model in default chain", target)
