# CUI // SP-CTI
"""The scenario picker is not cosmetic (fga-gd-03).

The GameDay audit claimed all 9 scenarios serve the same 6 ai_gameday injects.
It does not reproduce — every pack resolves to a distinct inject set. This test
pins that, so if the packs ever DO converge the claim becomes true and someone
finds out from a failing test rather than from a player.

Findings: docs/spikes/fga-gd-03-scenario-picker-verification.md
"""
from __future__ import annotations

import pytest

from tools.ttx.scenario_loader import _SCENARIOS_DIR, load_scenario


def _slugs() -> list[str]:
    return sorted(
        p.name for p in _SCENARIOS_DIR.iterdir() if (p / "scenario.yaml").exists()
    )


def _inject_key(scenario: dict) -> tuple:
    injects = scenario.get("injects") or []
    return tuple(sorted(
        str(i.get("inject_id") or i.get("id") or i.get("title"))[:60] for i in injects
    ))


def test_every_pack_resolves_a_distinct_inject_set():
    """The refutation. Two packs sharing a set would make the picker cosmetic."""
    seen: dict = {}
    for slug in _slugs():
        seen.setdefault(_inject_key(load_scenario(slug)), []).append(slug)

    shared = {k: v for k, v in seen.items() if len(v) > 1}
    assert not shared, f"packs serving identical injects: {list(shared.values())}"


def test_all_nine_packs_are_present():
    assert len(_slugs()) == 9, "the claim was made about 9 packs"


@pytest.mark.parametrize("slug", _slugs())
def test_each_pack_has_injects(slug):
    """An empty pack would look identical to every other empty pack."""
    assert load_scenario(slug).get("injects"), f"{slug} resolves no injects"


def test_an_unknown_slug_raises_rather_than_falling_back():
    """A silent fallback to the default pack is what would make it cosmetic."""
    with pytest.raises(FileNotFoundError):
        load_scenario("no-such-scenario-pack")
