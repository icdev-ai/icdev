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

# Local-copy mode (lpx-keys-04). A LOCAL CANVAS COPY runs one-per-person on a
# laptop. The real provider key must never be distributed into N local .env
# files, so a local copy MUST reach the hosted gateway with a per-person virtual
# key and MUST fail closed if it cannot — never silently fall back to a real key
# found in the environment.
ENV_LOCAL_COPY = "ICDEV_LLM_LOCAL_COPY"

# CUI egress gate (lpx-egress-02). The shared proxy is a NEW cloud egress path;
# by default it may carry ONLY unclassified traffic. Anything above the configured
# ceiling (CUI and up) must NOT silently traverse the proxy to a cloud provider —
# it stays local. An operator who has accredited the proxy for a higher level can
# raise this via ``ICDEV_LLM_PROXY_MAX_CLASSIFICATION`` (an ATO-boundary decision,
# documented in the feature doc), but the SAFE DEFAULT is fail-closed.
ENV_MAX_CLASSIFICATION = "ICDEV_LLM_PROXY_MAX_CLASSIFICATION"

# Ordinal clearance ranking, fail-CLOSED: an unknown/garbled label maps to a very
# high order (99) so it is treated as maximally sensitive and blocked, rather than
# fail-open (classification_manager defaults unknown -> CUI). Mirrors the intent of
# routing_policy._clearance_order without importing it (keep this module dependency
# -light and self-contained inside tools/llm/).
_CLEARANCE_ORDER = {
    "PUBLIC": 0, "UNCLASSIFIED": 0, "U": 0, "UNCLASS": 0,
    "IL2": 0,
    "CUI": 1, "CONTROLLED UNCLASSIFIED INFORMATION": 1, "IL4": 1,
    "ECI": 2, "IL5": 2,
    "CONFIDENTIAL": 3,
    "SECRET": 4, "IL6": 4,
    "TOP SECRET": 5, "TS": 5,
    "TOP SECRET//SCI": 6, "TS//SCI": 6, "TS/SCI": 6,
}

# By default the proxy may carry only UNCLASSIFIED/PUBLIC (order 0). CUI (1) and
# above are refused unless an operator explicitly raises the ceiling.
_DEFAULT_MAX_CLEARANCE = 0
_UNKNOWN_CLEARANCE = 99

_DEFAULT_BASE_URL = "http://localhost:4000"


class LocalCopyEgressError(RuntimeError):
    """A local canvas copy could not reach the gateway with a virtual key.

    Raised (fail-closed) instead of falling back to a real provider key. The
    message is operator-facing onboarding guidance.
    """


def is_proxy_enabled() -> bool:
    """True only when ``ICDEV_LLM_PROXY_ENABLED`` is explicitly truthy."""
    return os.environ.get(ENV_ENABLED, "").strip().lower() in _TRUTHY


def is_local_copy_mode() -> bool:
    """True when this install is a per-person LOCAL CANVAS COPY (lpx-keys-04)."""
    return os.environ.get(ENV_LOCAL_COPY, "").strip().lower() in _TRUTHY


def proxy_base_url() -> str:
    """Resolve the gateway base URL (loopback by default)."""
    return (os.environ.get(ENV_BASE_URL, "").strip() or _DEFAULT_BASE_URL).rstrip("/")


def _virtual_key_env_for(provider_cfg: Dict) -> str:
    """The env var NAME that holds this provider's virtual key (never the real one)."""
    return provider_cfg.get("proxy_api_key_env") or ENV_VIRTUAL_KEY


def _virtual_key_present(provider_cfg: Dict) -> bool:
    return bool(os.environ.get(_virtual_key_env_for(provider_cfg), "").strip())


def apply_gateway_to_provider_cfg(provider_name: str, provider_cfg: Dict) -> Dict:
    """Return a provider config redirected to the proxy, or the original.

    No-op (returns the SAME object) when the gateway is disabled AND local-copy
    mode is off, or the provider is not a redirectable cloud type. When active,
    returns a shallow COPY with ``base_url`` pointed at the proxy and
    ``api_key_env`` swapped for the virtual key env var — the original config dict
    is never mutated.

    Local-copy hardening (lpx-keys-04): in local-copy mode a cloud provider is
    redirected to the gateway **even if** ``ICDEV_LLM_PROXY_ENABLED`` is unset
    (a local copy has no other legitimate path), and ``api_key_env`` is FORCED to
    the virtual-key env var. This guarantees the real provider key is never read
    even if a caller swallows :func:`enforce_local_copy_egress` — the credential
    presented is always the virtual key (empty if unprovisioned, which fails
    closed at the gateway rather than leaking a real key).

    The model IDs are untouched (no hardcoding in Python — the routing chain and
    ``providers.<name>.models`` in ``args/llm_config.yaml`` remain the source of
    truth); LiteLLM's ``model_list`` maps them to the real upstream model.
    """
    local_copy = is_local_copy_mode()
    if not is_proxy_enabled() and not local_copy:
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
    cfg["api_key_env"] = _virtual_key_env_for(provider_cfg)
    return cfg


def enforce_local_copy_egress(provider_cfg: Dict) -> None:
    """Fail closed (with a clear message) when a local copy cannot egress safely.

    No-op unless local-copy mode is on. For a cloud provider type, raises
    :class:`LocalCopyEgressError` when no virtual key is provisioned — refusing to
    fall back to a real provider key. Call this OUTSIDE any broad ``except`` so
    the operator sees the guidance; :func:`apply_gateway_to_provider_cfg` already
    guarantees no real-key leak even if this raise is swallowed.
    """
    if not is_local_copy_mode():
        return
    ptype = str(provider_cfg.get("type", ""))
    if ptype not in _PROXYABLE_TYPES:
        return  # local (ollama)/GovCloud (bedrock) paths are untouched
    if not _virtual_key_present(provider_cfg):
        vk = _virtual_key_env_for(provider_cfg)
        raise LocalCopyEgressError(
            "Local canvas copy: no virtual key is set, so this copy CANNOT reach "
            "the shared LLM gateway — and it will NOT fall back to a real provider "
            f"key. Set {vk} to a per-person virtual key (issue one with "
            "`python tools/llm/proxy_keys.py issue ...`) and point "
            f"{ENV_BASE_URL} at the gateway. See the local-copy onboarding "
            "template (.env.local-copy.template)."
        )


def local_copy_preflight(timeout: float = 3.0) -> Dict:
    """Report whether a local copy is ready to egress: virtual key + reachable gateway.

    Returns a status dict (never raises) for onboarding/health checks:
        {local_copy, virtual_key_set, gateway_url, gateway_reachable, ok, message}
    """
    lc = is_local_copy_mode()
    vk_set = bool(os.environ.get(ENV_VIRTUAL_KEY, "").strip())
    url = proxy_base_url()
    reachable = _gateway_reachable(url, timeout) if lc else False
    ok = (vk_set and reachable) if lc else True
    if not lc:
        msg = "Not a local copy — no local-copy egress constraints apply."
    elif ok:
        msg = "Local copy ready: virtual key set and gateway reachable."
    elif not vk_set:
        msg = f"Local copy NOT ready: {ENV_VIRTUAL_KEY} is unset."
    else:
        msg = f"Local copy NOT ready: gateway {url} is unreachable (fail closed)."
    return {
        "local_copy": lc,
        "virtual_key_set": vk_set,
        "gateway_url": url,
        "gateway_reachable": reachable,
        "ok": ok,
        "message": msg,
    }


def _gateway_reachable(base_url: str, timeout: float) -> bool:
    """Best-effort TCP reachability probe of the gateway host:port. Never raises."""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── CUI egress gate (lpx-egress-02) ──────────────────────────────────────────

def _clearance_order(classification) -> int:
    """Ordinal clearance for a classification/impact label — fail CLOSED.

    Unknown/garbled labels return :data:`_UNKNOWN_CLEARANCE` (99) so they are
    treated as maximally sensitive and blocked from the proxy, never fail-open.
    """
    if not classification:
        # No label given: default is CUI (matches LLMRequest's default), so treat
        # as CUI — sensitive enough to be blocked by the default ceiling.
        return _CLEARANCE_ORDER["CUI"]
    norm = str(classification).strip().upper()
    if norm in _CLEARANCE_ORDER:
        return _CLEARANCE_ORDER[norm]
    # Tolerate common banner suffixes (e.g. "CUI // SP-CTI", "SECRET//NOFORN").
    head = norm.split("//")[0].strip()
    if head in _CLEARANCE_ORDER:
        return _CLEARANCE_ORDER[head]
    return _UNKNOWN_CLEARANCE


def proxy_max_clearance() -> int:
    """The highest clearance order permitted to traverse the proxy.

    Default is UNCLASSIFIED (0) — CUI and above are refused. An operator may raise
    it with ``ICDEV_LLM_PROXY_MAX_CLASSIFICATION`` (e.g. ``CUI``) only after
    accrediting the proxy for that level; an unrecognised value fails closed to 0.
    """
    raw = os.environ.get(ENV_MAX_CLASSIFICATION, "").strip()
    if not raw:
        return _DEFAULT_MAX_CLEARANCE
    norm = raw.upper()
    if norm in _CLEARANCE_ORDER:
        return _CLEARANCE_ORDER[norm]
    head = norm.split("//")[0].strip()
    return _CLEARANCE_ORDER.get(head, _DEFAULT_MAX_CLEARANCE)


def will_redirect(provider_cfg: Dict) -> bool:
    """True when the gateway WOULD redirect this provider to the proxy.

    Mirrors the activation condition in :func:`apply_gateway_to_provider_cfg`:
    proxy enabled OR local-copy mode, AND a redirectable cloud provider type. When
    False the CUI egress gate is a no-op (no proxy path exists for this call).
    """
    if not is_proxy_enabled() and not is_local_copy_mode():
        return False
    return str(provider_cfg.get("type", "")) in _PROXYABLE_TYPES


def proxy_egress_classification_block(provider_cfg: Dict, classification) -> str:
    """Return a refusal reason if this classification must NOT traverse the proxy.

    Pure predicate (no raise, no router dependency): returns a human-readable
    reason string when the proxy WOULD carry this provider's traffic AND the
    request's classification exceeds the configured proxy ceiling; otherwise
    returns ``""``. The caller (the router's invoke-time egress gate) turns a
    non-empty reason into a ``ForceLocalViolation`` so the call falls back to a
    local model instead of silently traversing the cloud proxy.

    Invoke-time by construction: ``classification`` comes from the live
    ``LLMRequest`` — it is deliberately NOT consulted at cached-provider
    construction, where no request (hence no classification) is available.
    """
    if not will_redirect(provider_cfg):
        return ""
    order = _clearance_order(classification)
    if order > proxy_max_clearance():
        label = str(classification).strip() if classification else "CUI (unlabelled default)"
        return (
            f"classification '{label}' may not traverse the shared LLM proxy — "
            f"classified/controlled content stays local (proxy ceiling: "
            f"{os.environ.get(ENV_MAX_CLASSIFICATION, '').strip() or 'UNCLASSIFIED'}). "
            "Route this via a local provider (Ollama) or a properly accredited "
            "GovCloud path (bedrock), which the proxy never redirects."
        )
    return ""
