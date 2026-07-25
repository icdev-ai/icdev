# CUI // SP-CTI
"""ICDEV LLM proxy gateway resolution (lpx-proxy-03).

OPT-IN, OFF BY DEFAULT. When the operator enables the LiteLLM proxy service
(``docker compose --profile llm-proxy up``; see ``lpx-proxy-02``) and sets
``ICDEV_LLM_PROXY_ENABLED=true``, this module redirects a *cloud* provider's
``base_url`` to the proxy and swaps its ``api_key_env`` for a **virtual key**, so
the real provider key never leaves the proxy. When the flag is unset/false every
provider resolves to its real endpoint exactly as before.

This is a DIFFERENT concern from ``tools/llm/proxy_resolver.py``:

* ``proxy_resolver`` reads the exact env var ``ICDEV_LLM_PROXY`` (and
  ``ICDEV_LLM_PROXY_CMD``) and pushes an HTTP CONNECT proxy into ``HTTPS_PROXY``
  for *corporate egress*. It is a transport-layer tunnel.
* This module reads ``ICDEV_LLM_PROXY_ENABLED`` / ``ICDEV_LLM_PROXY_BASE_URL`` /
  ``ICDEV_LLM_PROXY_VIRTUAL_KEY`` — a distinct namespace — and rewrites the
  provider *endpoint + credential* at the application layer.

The two compose cleanly: an egress proxy can tunnel the connection to an LLM
gateway. Neither reads the other's env keys.

CUI/air-gap safety: only ``anthropic``/``openai``/``gemini``/``azure_openai``
cloud provider types are ever redirected. ``ollama`` (local) and ``bedrock``
(GovCloud/CUI path) are left untouched, so enabling the gateway never turns the
air-gap or CUI path into a new cloud egress route (see ``lpx-egress-01`` /
``lpx-egress-02``).
"""

from __future__ import annotations

import os
from typing import Dict

# Cloud provider types whose endpoint may be redirected to the proxy. Local
# (ollama) and GovCloud/CUI (bedrock) types are deliberately excluded.
_PROXYABLE_TYPES = frozenset({"anthropic", "openai", "gemini", "azure_openai"})

# LiteLLM serves the Anthropic-native surface at the root ("/v1/messages") and
# the OpenAI-compatible surface under "/v1". The SDKs append their own paths, so
# each provider type needs the right base suffix.
_BASE_SUFFIX_BY_TYPE = {
    "anthropic": "",
    "openai": "/v1",
    "azure_openai": "/v1",
    "gemini": "/v1",
}

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Env var names (distinct from proxy_resolver's ICDEV_LLM_PROXY / _CMD).
ENV_ENABLED = "ICDEV_LLM_PROXY_ENABLED"
ENV_BASE_URL = "ICDEV_LLM_PROXY_BASE_URL"
ENV_VIRTUAL_KEY = "ICDEV_LLM_PROXY_VIRTUAL_KEY"

_DEFAULT_BASE_URL = "http://localhost:4000"


def is_proxy_enabled() -> bool:
    """True only when ``ICDEV_LLM_PROXY_ENABLED`` is explicitly truthy."""
    return os.environ.get(ENV_ENABLED, "").strip().lower() in _TRUTHY


def proxy_base_url() -> str:
    """Resolve the gateway base URL (loopback by default)."""
    return (os.environ.get(ENV_BASE_URL, "").strip() or _DEFAULT_BASE_URL).rstrip("/")


def apply_gateway_to_provider_cfg(provider_name: str, provider_cfg: Dict) -> Dict:
    """Return a provider config redirected to the proxy, or the original.

    No-op (returns the SAME object) when the gateway is disabled or the provider
    is not a redirectable cloud type. When active, returns a shallow COPY with
    ``base_url`` pointed at the proxy and ``api_key_env`` swapped for the virtual
    key env var — the original config dict is never mutated.

    The model IDs are untouched (no hardcoding in Python — the routing chain and
    ``providers.<name>.models`` in ``args/llm_config.yaml`` remain the source of
    truth); LiteLLM's ``model_list`` maps them to the real upstream model.
    """
    if not is_proxy_enabled():
        return provider_cfg
    ptype = str(provider_cfg.get("type", ""))
    if ptype not in _PROXYABLE_TYPES:
        return provider_cfg

    cfg = dict(provider_cfg)
    cfg["base_url"] = proxy_base_url() + _BASE_SUFFIX_BY_TYPE.get(ptype, "")
    # Per-provider override wins, else the global virtual-key env var. This
    # mirrors the api_key_env convention that already distinguishes ollama from
    # ollama_cloud (cli-bridge-cui-egress lesson): the env var *name* selects
    # which credential is presented — here a virtual key, never the real one.
    cfg["api_key_env"] = provider_cfg.get("proxy_api_key_env") or ENV_VIRTUAL_KEY
    return cfg
