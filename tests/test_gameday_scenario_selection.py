# CUI // SP-CTI
"""aicur-fix-04 — the League plays every scenario pack; no pack is hollow.

Two defects:
  * GameMaster iterated only CYBER_SCENARIOS, so the 14 AI-ops League scenarios
    (ace, rg, gc, dr, cx, di, nt, of, fz, gr) were registered but never played.
    Selection is now configured by ``scenario_selection.packs`` in
    args/gameday_teams.yaml and rotates round-robin across packs.
  * scenarios/interagency/scenario.yaml referenced 5 inject bodies (and its
    rubrics and personas) that did not exist. scenario_loader drops a missing
    file silently, so every inject loaded with no body and no rubric. The pack
    is now authored; the integrity test below covers every pack on disk.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from tools.gameday.constants import AI_OPS_SCENARIOS, CYBER_SCENARIOS
from tools.gameday.game_master import select_scenarios, selected_pack_names
from tools.ttx.scenario_loader import list_scenario_slugs, load_scenario

_REPO = Path(__file__).resolve().parent.parent
_SCENARIOS = _REPO / "scenarios"
_CYBER_BRIEFS = ("attack_brief", "defense_brief", "innovation_brief", "compliance_brief")


# ── League scenario selection ────────────────────────────────────────────────

def test_shipped_config_selects_every_pack():
    cfg = yaml.safe_load((_REPO / "args" / "gameday_teams.yaml").read_text(encoding="utf-8"))
    assert set(selected_pack_names(cfg)) == {"cyber_adversarial", *AI_OPS_SCENARIOS}


def test_every_scenario_is_selected_once():
    ids = [s["id"] for s in select_scenarios({"scenario_selection": {"packs": "all"}})]
    expected = [s["id"] for s in CYBER_SCENARIOS]
    expected += [s["id"] for pack in AI_OPS_SCENARIOS.values() for s in pack]
    assert sorted(ids) == sorted(expected)
    assert len(ids) == len(set(ids))


def test_rounds_rotate_across_packs():
    scenarios = select_scenarios({"scenario_selection": {"packs": "all"}})
    n_packs = 1 + len(AI_OPS_SCENARIOS)
    first = [s["pack"] for s in scenarios[:n_packs]]
    assert len(set(first)) == n_packs, first


def test_ai_ops_scenarios_reach_teams_with_their_own_briefs():
    from tools.gameday.team_runner import _build_prompt

    for s in select_scenarios({"scenario_selection": {"packs": "all"}}):
        for key in _CYBER_BRIEFS:
            assert s.get(key), f"{s['id']} has no {key}"
    ace = next(s for s in select_scenarios() if s["id"] == "ace-001")
    prompt = _build_prompt("gold", "builder", ace)
    assert AI_OPS_SCENARIOS["ace_showdown"][0]["gold_brief"] in prompt


def test_selection_does_not_mutate_the_registered_packs():
    select_scenarios()
    assert "attack_brief" not in AI_OPS_SCENARIOS["ace_showdown"][0]
    assert "pack" not in CYBER_SCENARIOS[0]


def test_explicit_pack_list():
    picked = select_scenarios({"scenario_selection": {"packs": ["graphrag_hunt"]}})
    assert [s["id"] for s in picked] == ["gr-001"]


@pytest.mark.parametrize("packs", [["no_such_pack"], []])
def test_bad_selection_fails_loud(packs):
    with pytest.raises(ValueError):
        select_scenarios({"scenario_selection": {"packs": packs}})


def test_run_tournament_plays_ai_ops_scenarios(monkeypatch):
    gm_mod = importlib.import_module("tools.gameday.game_master")
    db_mod = importlib.import_module("tools.gameday.db")
    rm_mod = importlib.import_module("tools.gameday.round_manager")

    played: list[str] = []

    class _FakeRoundManager:
        def __init__(self, tournament_id, ollama_url=None):
            pass

        def run_round(self, round_num, scenario):
            played.append(scenario["id"])
            return {"round": round_num}

    monkeypatch.setattr(gm_mod, "get_or_create_active_tournament",
                        lambda: {"id": 1, "config_json": "{}"})
    monkeypatch.setattr(db_mod, "update_tournament", lambda *a, **k: None)
    monkeypatch.setattr(rm_mod, "RoundManager", _FakeRoundManager)

    summary = gm_mod.GameMaster(round_count=3).run_tournament()

    assert summary["status"] == "completed"
    assert played == ["cs-001", "ace-001", "rg-001"]


# ── Scenario pack integrity ──────────────────────────────────────────────────

def _referenced_files(pack_dir: Path) -> list[str]:
    raw = yaml.safe_load((pack_dir / "scenario.yaml").read_text(encoding="utf-8"))
    refs = []
    for inj in raw.get("injects", []):
        refs.append(inj.get("body_path"))
        refs.append((inj.get("scoring") or {}).get("rubric"))
    refs += [r.get("persona_template") for r in raw.get("roles", [])]
    return [r for r in refs if isinstance(r, str)]


@pytest.mark.parametrize("slug", list_scenario_slugs())
def test_every_referenced_pack_file_exists(slug):
    pack_dir = _SCENARIOS / slug
    missing = [r for r in _referenced_files(pack_dir) if not (pack_dir / r).exists()]
    assert not missing, f"{slug} references files that do not exist: {missing}"


def test_interagency_loads_fully_resolved():
    sc = load_scenario("interagency")
    assert len(sc["injects"]) == 5
    for inj in sc["injects"]:
        assert inj.get("body_md"), f"{inj['id']} has no body"
        rubric = inj["scoring"]["rubric"]
        assert isinstance(rubric, dict), f"{inj['id']} rubric not resolved"
        weights = [d["weight"] for d in rubric["dimensions"]]
        assert abs(sum(weights) - 1.0) < 1e-9, f"{inj['id']} weights sum {sum(weights)}"
    for role in sc["roles"]:
        assert role.get("persona_data", {}).get("prompt_hint"), f"{role['id']} persona unresolved"
