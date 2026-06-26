# CUI // SP-CTI
"""GameDay scenario pack loader — unified access to all scenario collections."""
from __future__ import annotations

from tools.gameday.constants import (
    CYBER_SCENARIOS,
    AI_OPS_SCENARIOS,
)

ALL_SCENARIO_PACKS: dict[str, list[dict]] = {
    "cyber_adversarial": CYBER_SCENARIOS,
    **AI_OPS_SCENARIOS,
}


def get_scenario(pack: str, scenario_id: str) -> dict | None:
    """Return a scenario dict by pack name and scenario ID, or None."""
    scenarios = ALL_SCENARIO_PACKS.get(pack, [])
    return next((s for s in scenarios if s.get("id") == scenario_id), None)


def list_packs() -> list[str]:
    """Return list of available scenario pack names."""
    return list(ALL_SCENARIO_PACKS.keys())


def list_scenarios(pack: str) -> list[dict]:
    """Return all scenarios in a pack."""
    return ALL_SCENARIO_PACKS.get(pack, [])


def get_pack_metadata() -> list[dict]:
    """Return pack names with scenario counts."""
    return [
        {
            "pack": pack,
            "count": len(scenarios),
            "ids": [s.get("id") for s in scenarios],
        }
        for pack, scenarios in ALL_SCENARIO_PACKS.items()
    ]
