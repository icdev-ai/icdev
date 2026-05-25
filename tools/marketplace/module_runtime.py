# CUI // SP-CTI
"""Module Runtime — gating logic for marketplace modules.

Determines whether a marketplace module is enabled based on the
current operating mode and locally cached license tokens.

Per D-MKT-S4: this is part of the 4-file thin client
(module_runtime, license_client, token_store, asset_installer).

Per D-MKT-S2: Two modes:
  - oss: all modules unlocked (default)
  - saas: token verification required

Per D-MKT-D1: When marketplace is disabled, is_module_enabled() returns True.

Usage:
    from tools.marketplace.module_runtime import is_module_enabled
    if is_module_enabled("writeguard"):
        # module features available
        pass
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from typing import Set

logger = get_logger("marketplace.module_runtime")

# Operating mode: "oss" (all unlocked) or "saas" (token-gated)
MODE = os.environ.get("ICDEV_MARKETPLACE_MODE", "oss")

# In-memory cache of enabled slugs (invalidated by invalidate_cache())
_cache: Set[str] | None = None


def is_module_enabled(slug: str) -> bool:
    """Check whether a marketplace module is enabled.

    In 'oss' mode (default), all modules are enabled.
    In 'saas' mode, checks locally cached license tokens for an active,
    non-expired token for the given slug (with 30-day grace period per
    D-MKT-S3).

    Args:
        slug: The module/asset slug to check.

    Returns:
        True if the module is enabled, False otherwise.
    """
    # D-MKT-D1: marketplace disabled = everything enabled
    if not _marketplace_enabled():
        return True

    current_mode = MODE
    if current_mode == "oss":
        return True

    # saas mode: check token store
    active = _get_active_slugs_cached()
    return slug in active


def invalidate_cache() -> None:
    """Clear the in-memory slug cache.

    Call this after token store updates (sync, renew, etc.) to force
    a fresh read on the next is_module_enabled() check.
    """
    global _cache
    _cache = None


def _marketplace_enabled() -> bool:
    """Check if marketplace is enabled via env var or config."""
    env_val = os.environ.get("ICDEV_MARKETPLACE_ENABLED", "")
    if env_val.lower() in ("true", "1", "yes"):
        return True
    if env_val.lower() in ("false", "0", "no"):
        return False
    # Default: disabled (D-MKT-D1)
    return False


def _get_active_slugs_cached() -> Set[str]:
    """Return cached set of active slugs, loading from token_store if needed."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        from tools.marketplace.token_store import get_active_slugs

        _cache = set(get_active_slugs())
    except Exception:
        _cache = set()
    return _cache
