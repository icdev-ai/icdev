# CUI // SP-CTI
"""ACE canvas constants — loaded from args/ace/ace_config.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "args" / "ace" / "ace_config.yaml"


def _load() -> dict:
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


_cfg = _load()

MAX_TEAM_SIZE: int = int(_cfg.get("max_team_size", 8))
MAX_NEGOTIATION_ROUNDS: int = int(_cfg.get("max_negotiation_rounds", 3))
HITL_THRESHOLD: float = float(_cfg.get("hitl_threshold", 0.6))
TRUST_TIER_DEFAULT: str = str(_cfg.get("trust_tier_default", "yellow"))
STALE_INSTANCE_HOURS: int = int(_cfg.get("stale_instance_hours", 4))
HOT_RELOAD_ROLES: bool = bool(_cfg.get("hot_reload_roles", True))
