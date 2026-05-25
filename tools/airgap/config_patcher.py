#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Air-gap config patcher — rewrites LLM routing for local-only operation.

When activated, this module:
1. Patches routing chains to use only local models (any local LLM server)
2. Disables two-tier tier2 (cloud) — scanner-only mode
3. Sets prefer_local in router settings
4. Patches hook environment variables for non-Claude-Code sessions
5. Registers local PDF fallback provider

Supports ANY local LLM server: Ollama, vLLM, llama.cpp, LM Studio,
LocalAI, TGI, or any OpenAI-compatible endpoint on localhost/private net.
"Local" is determined by the provider's base_url, not its type.

Does NOT modify YAML files on disk — all patches are in-memory at runtime.

Usage::

    from tools.airgap.config_patcher import activate_airgap, deactivate_airgap

    activate_airgap()      # patches all subsystems
    deactivate_airgap()    # restores original config
"""

import copy
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.airgap.config_patcher")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── State ──────────────────────────────────────────────────────────────
_active = False
_original_config: Optional[Dict[str, Any]] = None
_original_env: Dict[str, str] = {}


def _is_provider_local(provider_cfg: dict) -> bool:
    """Determine if a provider is local based on its base_url or type.

    A provider is local if:
    1. Its type is 'ollama' (always local by design), OR
    2. Its base_url resolves to localhost / private network address

    This covers: Ollama, vLLM, llama.cpp server, LM Studio, LocalAI,
    TGI, and any other OpenAI-compatible server on the local network.
    """
    ptype = provider_cfg.get("type", "")

    # Ollama is always local
    if ptype == "ollama":
        return True

    # For openai_compatible and others, check the base_url
    base_url = provider_cfg.get("base_url", "")
    if not base_url:
        return False

    # Expand env vars in base_url (e.g. ${VLLM_BASE_URL:-http://localhost:8000/v1})
    import re

    for m in re.finditer(r"\$\{([^}]+)\}", base_url):
        expr = m.group(1)
        var, _, default = expr.partition(":-")
        base_url = base_url.replace(m.group(0), os.environ.get(var, default))

    try:
        from tools.airgap.detector import is_local_url

        return is_local_url(base_url)
    except ImportError:
        # Inline fallback if detector not importable
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")  # nosec B104 -- intentional bind-all for containerized/dev deployment


def _get_local_models(config: dict) -> List[str]:
    """Extract all model names backed by local LLM providers.

    "Local" means the provider's base_url points to localhost or a
    private network address. This is provider-type-agnostic — works
    with Ollama, vLLM, llama.cpp, LM Studio, LocalAI, TGI, etc.
    """
    models = config.get("models", {})
    providers = config.get("providers", {})

    local_models = []
    for name, mcfg in models.items():
        pname = mcfg.get("provider", "")
        pcfg = providers.get(pname, {})
        if _is_provider_local(pcfg):
            local_models.append(name)

    return local_models


def _rewrite_routing_chains(config: dict) -> dict:
    """Rewrite all routing chains to only use local models.

    For each function route, filters the chain to keep only local models.
    If no local model remains, inserts the default local fallback.
    """
    local_models = set(_get_local_models(config))
    if not local_models:
        logger.warning("No local models found in config — air-gap routing may fail")
        return config

    # Determine best default fallback
    preferred_order = [
        "qwen3-local",
        "qwen3.5-local",
        "qwen3-dual",
        "llama-local",
        "codestral-local",
        "gemma3-local",
        "deepseek-local",
        "phi4-local",
    ]
    default_fallback = None
    for m in preferred_order:
        if m in local_models:
            default_fallback = m
            break
    if not default_fallback and local_models:
        default_fallback = sorted(local_models)[0]

    routing = config.get("routing", {})
    for func_name, route in routing.items():
        chain = route.get("chain", [])
        local_chain = [m for m in chain if m in local_models]
        if not local_chain and default_fallback:
            local_chain = [default_fallback]
        route["chain"] = local_chain

    return config


def _patch_two_tier(config: dict) -> dict:
    """Patch two-tier config: make tier2 also local (no Claude review).

    In air-gap mode:
    - planner_functions → use tier1 directly (no Claude)
    - worker_functions → tier1 draft only (no Claude review)
    - scanner_functions → unchanged (already local)
    """
    tt = config.get("two_tier", {})
    if not tt.get("enabled", False):
        return config

    local_models = set(_get_local_models(config))

    # Check if tier1 is local
    tier1 = tt.get("tier1_model", "qwen3-local")
    if tier1 not in local_models:
        # Find best local replacement
        for m in ["qwen3-local", "qwen3.5-local", "qwen3-dual"]:
            if m in local_models:
                tt["tier1_model"] = m
                break

    # Make tier2 also local — use the best available local model
    # or disable two-tier entirely to just use chain routing
    tier2_candidates = [
        "phi4-local",
        "deepseek-local",
        "qwen3-local",
        "llama-local",
    ]
    local_tier2 = None
    for m in tier2_candidates:
        if m in local_models and m != tt.get("tier1_model"):
            local_tier2 = m
            break

    if local_tier2:
        tt["tier2_model"] = local_tier2
        logger.info("Air-gap: tier2 remapped to local model %s", local_tier2)
    else:
        # Only one local model — disable two-tier to use single-model chain
        tt["enabled"] = False
        logger.info("Air-gap: two-tier disabled (insufficient local models)")

    return config


def _patch_embeddings(config: dict) -> dict:
    """Filter embedding chain to only local providers."""
    emb = config.get("embeddings", {})
    chain = emb.get("default_chain", [])
    models = emb.get("models", {})
    providers = config.get("providers", {})

    local_chain = []
    for name in chain:
        mcfg = models.get(name, {})
        pname = mcfg.get("provider", "")
        pcfg = providers.get(pname, {})
        if _is_provider_local(pcfg):
            local_chain.append(name)

    if local_chain:
        emb["default_chain"] = local_chain
    elif chain:
        logger.warning("Air-gap: no local embedding models — embedding search unavailable")
        emb["default_chain"] = []

    return config


def _set_env_shims() -> None:
    """Set environment variable shims for non-Claude-Code sessions."""
    import uuid

    # Session ID — hooks use this for event correlation
    if not os.environ.get("CLAUDE_SESSION_ID"):
        session_id = f"airgap-{uuid.uuid4().hex[:12]}"
        os.environ["CLAUDE_SESSION_ID"] = session_id
        _original_env["CLAUDE_SESSION_ID"] = ""
        logger.debug("Air-gap: set CLAUDE_SESSION_ID=%s", session_id)

    # Project directory — hooks use this for file resolution
    if not os.environ.get("CLAUDE_PROJECT_DIR"):
        os.environ["CLAUDE_PROJECT_DIR"] = str(BASE_DIR)
        _original_env["CLAUDE_PROJECT_DIR"] = ""
        logger.debug("Air-gap: set CLAUDE_PROJECT_DIR=%s", BASE_DIR)

    # Force air-gap flag
    os.environ["ICDEV_AIRGAP"] = "true"
    _original_env.setdefault("ICDEV_AIRGAP", "")

    # Disable two-tier via env (belt + suspenders with config patch)
    if not os.environ.get("LLM_TWO_TIER_ENABLED"):
        os.environ["LLM_TWO_TIER_ENABLED"] = "false"
        _original_env.setdefault("LLM_TWO_TIER_ENABLED", "")


def _restore_env() -> None:
    """Restore original environment variables."""
    for key, original_val in _original_env.items():
        if original_val:
            os.environ[key] = original_val
        else:
            os.environ.pop(key, None)
    _original_env.clear()


def activate_airgap(router=None) -> Dict[str, Any]:
    """Activate air-gap mode — patch all subsystems for local-only operation.

    Args:
        router: Optional LLMRouter instance to patch. If None, patches the
                global singleton from tools.llm.

    Returns:
        Dict with activation details (local_models, patched_functions, etc.)
    """
    global _active, _original_config

    if _active:
        logger.info("Air-gap mode already active")
        return {"status": "already_active"}

    # Set environment shims first
    _set_env_shims()

    # Get or create router
    if router is None:
        try:
            from tools.llm import get_router

            router = get_router()
        except ImportError:
            logger.error("Cannot import LLM router — air-gap activation incomplete")
            return {"status": "error", "reason": "router_import_failed"}

    # Save original config for deactivate
    _original_config = copy.deepcopy(router._config)

    # Apply patches
    config = router._config

    config = _rewrite_routing_chains(config)
    config = _patch_two_tier(config)
    config = _patch_embeddings(config)

    # Set prefer_local in settings
    config.setdefault("settings", {})["prefer_local"] = True

    # Apply patched config back to router
    router._config = config
    # Clear availability cache to force re-probe with new config
    router._availability_cache = {}
    router._availability_cache_time = 0.0

    _active = True

    # Collect stats
    local_models = _get_local_models(config)
    routing = config.get("routing", {})
    patched_functions = list(routing.keys())

    result = {
        "status": "activated",
        "local_models": local_models,
        "patched_functions_count": len(patched_functions),
        "two_tier_enabled": config.get("two_tier", {}).get("enabled", False),
        "prefer_local": True,
        "env_shims": list(_original_env.keys()),
    }

    logger.info("Air-gap mode ACTIVATED: %d local models, %d routes patched", len(local_models), len(patched_functions))
    return result


def deactivate_airgap(router=None) -> Dict[str, Any]:
    """Deactivate air-gap mode — restore original config.

    Args:
        router: Optional LLMRouter instance. If None, uses global singleton.

    Returns:
        Dict with deactivation status.
    """
    global _active, _original_config

    if not _active:
        return {"status": "not_active"}

    # Restore environment
    _restore_env()

    # Restore router config
    if _original_config is not None:
        if router is None:
            try:
                from tools.llm import get_router

                router = get_router()
            except ImportError:
                pass

        if router is not None:
            router._config = _original_config
            router._availability_cache = {}
            router._availability_cache_time = 0.0

    _original_config = None
    _active = False

    logger.info("Air-gap mode DEACTIVATED — original config restored")
    return {"status": "deactivated"}


def is_active() -> bool:
    """Check if air-gap mode is currently active."""
    return _active


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    result = activate_airgap()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "activated" else 1)
