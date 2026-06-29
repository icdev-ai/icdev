# CUI // SP-CTI
"""
Per-role cost cap loader for agent loop governance.

Reads role_cost_caps from args/llm_config.yaml (under agent_loop:) and
returns the cap for a given role, with fallback to the 'default' key or None.

Usage:
  from icdev.tools.llm.role_cost_caps import get_cap_for_role
  cap = get_cap_for_role("ai_developer")  # e.g. 2.00 or None
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "args" / "llm_config.yaml"


def _load_caps() -> dict:
    """Load role_cost_caps section; return empty dict on error."""
    try:
        import yaml

        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        # Primary location: agent_loop.role_cost_caps
        agent_loop_section = data.get("agent_loop", {})
        caps = agent_loop_section.get("role_cost_caps", {})
        if not caps:
            # Backward-compat: top-level role_cost_caps
            caps = data.get("role_cost_caps", {})
        return caps if isinstance(caps, dict) else {}
    except Exception:
        logger.debug("role_cost_caps: failed to load config (returning no cap)", exc_info=True)
        return {}


def get_cap_for_role(role_id: str) -> float | None:
    """
    Return the cost cap (USD) for the given role, or None if uncapped.

    Lookup order:
      1. Exact match on role_id in role_cost_caps
      2. 'default' key in role_cost_caps
      3. None (no cap applied)
    """
    caps = _load_caps()
    if role_id in caps and caps[role_id] is not None:
        try:
            return float(caps[role_id])
        except (TypeError, ValueError):
            return None
    if "default" in caps and caps["default"] is not None:
        try:
            return float(caps["default"])
        except (TypeError, ValueError):
            return None
    return None


def list_caps() -> dict[str, float | None]:
    """Return all configured role caps, excluding 'default' (for ops/status display)."""
    caps = _load_caps()
    result: dict[str, float | None] = {}
    for role, val in caps.items():
        if role == "default":
            continue
        try:
            result[role] = float(val) if val is not None else None
        except (TypeError, ValueError):
            result[role] = None
    return result
