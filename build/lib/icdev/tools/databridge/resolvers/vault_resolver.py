# CUI // SP-CTI
"""HashiCorp Vault KV secret resolver.

Resolves ``vault:path/to/secret#field`` refs via the hvac client.
VAULT_ADDR and VAULT_TOKEN are read from the environment — never
hardcoded.  Secrets are cached in-process for 5 minutes to avoid
hammering the Vault API on every connection open.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from threading import Lock
from typing import Any, Dict, Optional

logger = get_logger("databridge.resolvers.vault")

# ---------------------------------------------------------------------------
# In-process TTL cache (keyed by secret_ref) — cachetools.TTLCache
# ---------------------------------------------------------------------------

try:
    from cachetools import TTLCache as _TTLCache  # type: ignore[import-untyped]
    _cache: Any = _TTLCache(maxsize=256, ttl=300)
    _negative_cache: Any = _TTLCache(maxsize=256, ttl=30)  # 30s back-off for failures
except ImportError:
    _cache = {}  # fallback: no expiry, but never crashes
    _negative_cache = {}

_cache_lock = Lock()
_CACHE_TTL = 300  # 5 minutes
_NEGATIVE_CACHE_TTL = 30  # 30 seconds — prevents hammering Vault on repeated failures


def _cache_get(key: str) -> tuple[bool, Optional[str]]:
    """Return (hit, value). hit=False when missing or expired."""
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            return True, value
        return False, None


def _cache_set(key: str, value: str) -> None:
    """Store value; TTLCache handles expiry automatically."""
    with _cache_lock:
        _cache[key] = value


def _negative_cache_get(key: str) -> tuple[bool, Optional[str]]:
    """Return (hit, error_reason). hit=True when a recent failure is cached."""
    with _cache_lock:
        reason = _negative_cache.get(key)
        if reason is not None:
            return True, reason
        return False, None


def _negative_cache_set(key: str, reason: str) -> None:
    """Cache a failure reason for 30 s to suppress repeated Vault hammering."""
    with _cache_lock:
        _negative_cache[key] = reason or "unknown error"


def _clear_caches() -> None:
    """Clear both positive and negative caches. Intended for tests only."""
    with _cache_lock:
        _cache.clear()
        _negative_cache.clear()


# ---------------------------------------------------------------------------
# hvac lazy-import (module-level so tests can monkeypatch _hvac / _HVAC_AVAILABLE)
# ---------------------------------------------------------------------------

try:
    import hvac as _hvac  # type: ignore[import-untyped]
    _HVAC_AVAILABLE = True
except ImportError:
    _hvac = None  # type: ignore[assignment]
    _HVAC_AVAILABLE = False


class SecretResolverError(Exception):
    """Raised when the Vault resolver cannot return a value."""


def resolve(secret_ref: str) -> str:
    """Resolve a ``vault:path/to/secret#field`` reference.

    Args:
        secret_ref: Full reference including ``vault:`` prefix,
            e.g. ``vault:secret/db/prod#password``.

    Returns:
        Plaintext secret value (never empty).

    Raises:
        SecretResolverError: on misconfiguration, network failure, or
            missing field.
    """
    if not secret_ref.startswith("vault:"):
        raise SecretResolverError(f"Not a vault ref: {secret_ref!r}")

    ref_body = secret_ref[len("vault:"):]
    if "#" not in ref_body:
        raise SecretResolverError(
            f"vault ref is missing #field separator — got {secret_ref!r}"
        )

    secret_path, field = ref_body.rsplit("#", 1)
    if not secret_path or not field:
        raise SecretResolverError(
            f"Empty path or field in vault ref {secret_ref!r}"
        )

    neg_hit, neg_reason = _negative_cache_get(secret_ref)
    if neg_hit:
        raise SecretResolverError(
            f"Vault fetch suppressed (back-off 30s — previous error: {neg_reason})"
        )

    hit, cached_val = _cache_get(secret_ref)
    if hit:
        logger.debug("Vault cache hit for %r", secret_ref)
        return cached_val  # type: ignore[return-value]

    try:
        value = _fetch(secret_path, field, secret_ref)
    except SecretResolverError as exc:
        _negative_cache_set(secret_ref, str(exc))
        raise
    _cache_set(secret_ref, value)
    return value


def _fetch(secret_path: str, field: str, secret_ref: str) -> str:
    """Do the actual Vault read — no caching."""
    vault_addr = os.environ.get("VAULT_ADDR", "").strip()
    vault_token = os.environ.get("VAULT_TOKEN", "").strip()

    if not vault_addr:
        raise SecretResolverError("VAULT_ADDR environment variable is not set")
    if not vault_token:
        raise SecretResolverError("VAULT_TOKEN environment variable is not set")

    if not _HVAC_AVAILABLE:
        raise SecretResolverError(
            "hvac package is not installed — cannot use vault resolver. "
            "Install it: pip install hvac"
        )

    try:
        client = _hvac.Client(url=vault_addr, token=vault_token)
        data: Optional[Dict[str, Any]] = client.read(secret_path)
    except ConnectionError as exc:
        raise SecretResolverError(
            f"Vault connection error for {secret_ref!r}: {exc}"
        ) from exc
    except SecretResolverError:
        raise
    except Exception as exc:
        raise SecretResolverError(
            f"Vault read failed for {secret_ref!r}: {exc}"
        ) from exc

    if data is None:
        raise SecretResolverError(
            f"Vault path not found: {secret_path!r} (ref={secret_ref!r})"
        )

    kv_data: Dict[str, Any] = data.get("data", {})
    # KV v2 wraps values under an additional "data" key
    if "data" in kv_data and isinstance(kv_data["data"], dict):
        kv_data = kv_data["data"]

    if field not in kv_data:
        raise SecretResolverError(
            f"Field {field!r} not present in Vault secret {secret_path!r}. "
            f"Available: {list(kv_data.keys())}"
        )

    value = str(kv_data[field])
    if not value:
        raise SecretResolverError(
            f"Vault returned empty value for field {field!r} in {secret_ref!r}"
        )

    return value
