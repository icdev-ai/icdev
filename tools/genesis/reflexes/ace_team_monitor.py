# CUI // SP-CTI
"""Genesis reflex: ace_team_monitor — thin proxy to icdev.tools.ace.genesis_reflex.

Delegates to the canonical implementation in the ACE package so the genesis
daemon can dispatch it as ``tools.genesis.reflexes.ace_team_monitor.run``.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
CADENCE_HOURS = 4

from typing import Any


def run(config: dict, trust: Any) -> dict:
    """Dispatch to icdev.tools.ace.genesis_reflex.run."""
    try:
        from icdev.tools.ace import genesis_reflex  # noqa: PLC0415
        result = genesis_reflex.run(config, None)
        stale = result.get("stale_running", 0) + result.get("stale_hitl", 0)
        return {
            "success": True,
            "metric_value": float(stale),
            "details": result,
        }
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(exc)},
        }


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2))
