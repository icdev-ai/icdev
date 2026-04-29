# CUI // SP-CTI
"""HashiCorp Vault secret resolver for DataBridge.

Resolves secret refs of the form  vault:path/to/secret#field
using the hvac client against VAULT_ADDR with VAULT_TOKEN auth.
Results are cached for 5 minutes via TTLCache.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("databridge.resolvers.vault")


class SecretResolverError(Exception):
    """Raised when a secret reference cannot be resolved."""


# ---------------------------------------------------------------------------
# TTL cache — cachetools.TTLCache preferred; plain-dict fallback if absent
# ---------------------------------------------------------------------------
try:
    from cachetools import TTLCache as _TTLCache

    _cache: Any = _TTLCache(maxsize=256, ttl=300)

    def _cache_get(key: str) -> Tuple[bool, Optional[str]]:
        try:
            return True, _cache[key]
        except KeyError:
            return False, None

    def _cache_set(key: str, value: str) -> None:
        _cache[key] = value

except ImportError:
    # Minimal TTL store: {key: (value, expires_at)}
    _fallback: Dict[str, Tuple[str, float]] = {}

    def _cache_get(key: str) -> Tuple[bool, Optional[str]]:  # type: ignore[misc]
        entry = _fallback.get(key)
        if entry is None:
            return False, None
        value, expires = entry
        if time.monotonic() > expires:
            del _fallback[key]
            return False, None
        return True, value

    def _cache_set(key: str, value: str) -> None:  # type: ignore[misc]
        _fallback[key] = (value, time.monotonic() + 300)


# ---------------------------------------------------------------------------
# hvac import — optional; error surfaced at resolution time, not import time
# ---------------------------------------------------------------------------
try:
    import hvac as _hvac  # type: ignore[import-untyped]

    _HVAC_AVAILABLE = True
except ImportError:
    _hvac = None  # type: ignore[assignment]
    _HVAC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(secret_ref: str) -> str:
    """Resolve a ``vault:path/to/secret#field`` reference to plaintext.

    Raises:
        SecretResolverError: on any failure (missing env, connection error,
                             field not found, or empty value).  Never returns
                             an empty string.
    """
    if not secret_ref.startswith("vault:"):
        raise SecretResolverError(f"Not a vault ref: {secret_ref!r}")

    body = secret_ref[6:]  # strip leading "vault:"
    if "#" not in body:
        raise SecretResolverError(
            f"Invalid vault ref (missing #field): {secret_ref!r}"
        )

    path, field = body.rsplit("#", 1)
    if not path or not field:
        raise SecretResolverError(f"Empty path or field in vault ref: {secret_ref!r}")

    cache_key = f"{path}#{field}"
    hit, cached_value = _cache_get(cache_key)
    if hit:
        return cached_value  # type: ignore[return-value]

    value = _fetch(path, field)
    _cache_set(cache_key, value)
    return value


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _fetch(path: str, field: str) -> str:
    """Fetch *field* from Vault *path* via hvac."""
    addr = os.environ.get("VAULT_ADDR", "").rstrip("/")
    token = os.environ.get("VAULT_TOKEN", "")

    if not addr:
        raise SecretResolverError("VAULT_ADDR environment variable is not set")
    if not token:
        raise SecretResolverError("VAULT_TOKEN environment variable is not set")
    if not _HVAC_AVAILABLE:
        raise SecretResolverError(
            "hvac package is not installed; run: pip install 'hvac>=2.0'"
        )

    try:
        client = _hvac.Client(url=addr, token=token)
        response = client.read(path)
    except Exception as exc:
        raise SecretResolverError(
            f"Vault connection error reading {path!r}: {exc}"
        ) from exc

    if response is None:
        raise SecretResolverError(f"Vault path not found: {path!r}")

    data: Dict[str, Any] = response.get("data", {})
    # KV v2 wraps the payload under an inner "data" key
    if "data" in data and isinstance(data["data"], dict):
        data = data["data"]

    if field not in data:
        raise SecretResolverError(
            f"Field {field!r} not present in Vault path {path!r}"
        )

    value = data[field]
    if not value:
        raise SecretResolverError(
            f"Vault returned empty value for {path!r}#{field!r}"
        )

    return str(value)
