# CUI // SP-CTI
"""Backward-compat shim: tools.billing.tier -> icdev.tools.billing.tier."""
from icdev.tools.billing.tier import TIER_ORDER, get_active_tier, tier_satisfies

__all__ = ["TIER_ORDER", "get_active_tier", "tier_satisfies"]
