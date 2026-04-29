# CUI // SP-CTI
"""HashiCorp Vault KV secret resolver.

Resolves ``vault:path/to/secret#field`` refs via the hvac client.
VAULT_ADDR and VAULT_TOKEN are read from the environment — never
hardcoded.  Secrets are cached in-process for 5 minutes to avoid
hammering the Vault API on every connection open.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("databridge.resolvers.vault")

# (secret_ref, resolved_value, expires_at)
_cache: Dict[str, Tuple[str, float]] = {}
_cache_lock = Lock()
_CACHE_TTL = 300  # 5 minutes


class SecretResolverError(Exception):
    """Raised when the Vault resolver cannot return a value."""


def resolve(secret_ref: str) -> str:
    """Resolve a ``vault:path/to/secret#field`` reference.

    Args:
        secret_ref: Full reference including ``vault:`` prefix,
            e.g. ``vault:secret/db/prod#password``.

    Returns:
        Plaintext secret value.

    Raises:
        SecretResolverError: on misconfiguration, network failure, or
            missing field.
    """
    # Check in-process cache first
    with _cache_lock:
        cached = _cache.get(secret_ref)
        if cached and time.monotonic() < cached[1]:
            logger.debug("Vault cache hit for %r", secret_ref)
            return cached[0]

    value = _fetch(secret_ref)

    with _cache_lock:
        _cache[secret_ref] = (value, time.monotonic() + _CACHE_TTL)

    return value


def _fetch(secret_ref: str) -> str:
    """Do the actual Vault read — no caching."""
    try:
        import hvac  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SecretResolverError(
            "hvac is not installed — cannot use vault resolver. "
            "Install it: pip install hvac"
        ) from exc

    vault_addr = os.environ.get("VAULT_ADDR", "").strip()
    vault_token = os.environ.get("VAULT_TOKEN", "").strip()

    if not vault_addr:
        raise SecretResolverError("VAULT_ADDR environment variable is not set")
    if not vault_token:
        raise SecretResolverError("VAULT_TOKEN environment variable is not set")

    # Parse vault:path/to/secret#field
    ref_body = secret_ref[len("vault:"):]
    if "#" in ref_body:
        secret_path, field = ref_body.rsplit("#", 1)
    else:
        secret_path, field = ref_body, None

    try:
        client = hvac.Client(url=vault_addr, token=vault_token)
        if not client.is_authenticated():
            raise SecretResolverError("Vault authentication failed — check VAULT_TOKEN")

        data: Optional[Dict[str, Any]] = client.read(secret_path)
        if data is None:
            raise SecretResolverError(f"Secret path not found in Vault: {secret_path!r}")

        kv_data: Dict[str, Any] = data.get("data", {})
        # KV v2 wraps values under an additional "data" key
        if "data" in kv_data and isinstance(kv_data["data"], dict):
            kv_data = kv_data["data"]

        if field:
            if field not in kv_data:
                raise SecretResolverError(
                    f"Field {field!r} not found in Vault secret {secret_path!r}. "
                    f"Available fields: {list(kv_data.keys())}"
                )
            value = str(kv_data[field])
        else:
            # No field specified — return the first (and usually only) value
            if not kv_data:
                raise SecretResolverError(f"Vault secret {secret_path!r} has no data fields")
            value = str(next(iter(kv_data.values())))

        if not value:
            raise SecretResolverError(f"Vault returned empty value for {secret_ref!r}")

        return value

    except SecretResolverError:
        raise
    except Exception as exc:
        raise SecretResolverError(
            f"Vault read failed for {secret_ref!r}: {exc}"
        ) from exc
