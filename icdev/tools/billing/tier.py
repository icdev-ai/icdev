# CUI // SP-CTI
"""License tier utilities for ICDEV™.

Reads ICDEV_LICENSE_TIER from the environment (set via profile_env_overrides or
directly in .env). The active tier gates min_tier-filtered component access.
"""

from __future__ import annotations

import os

TIER_ORDER: list[str] = ["community", "professional", "enterprise"]
_VALID_TIERS: frozenset[str] = frozenset(TIER_ORDER)


def get_active_tier() -> str:
    """Return the active license tier.

    Resolution order:
      1. ICDEV_LICENSE_TIER env var (set by profile apply or .env)
      2. Default: 'community'
    """
    tier = os.environ.get("ICDEV_LICENSE_TIER", "community")
    if tier not in _VALID_TIERS:
        return "community"
    return tier


def tier_satisfies(active: str, required: str) -> bool:
    """Return True if *active* tier is >= *required* tier in TIER_ORDER."""
    try:
        return TIER_ORDER.index(active) >= TIER_ORDER.index(required)
    except ValueError:
        return False
