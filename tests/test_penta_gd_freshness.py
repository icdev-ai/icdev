# CUI // SP-CTI
"""penta-gd-07 — GameDay content freshness / consistency tests.

Complements tests/test_penta_gd_content.py (which checks the 6 *new* AI-Ops
packs + the AI_TOOLS_CATALOG mcp_tool resolution) by widening coverage to the
*whole* surface:

  * Every pack in ALL_SCENARIO_PACKS (not just the new ones) carries a complete
    scenario schema — accepting either the AI-Ops brief shape
    (red/blue/gold/green) or the cyber-adversarial shape
    (attack/defense/innovation/compliance).
  * Every AI_TOOLS_CATALOG mcp_tool resolves to a *well-formed* TOOL_REGISTRY
    entry (dict with module + handler), not merely a present key.
  * Every scenario slug from scenario_loader.list_scenario_slugs() loads without
    error (loader tolerance across legacy + new packs).
  * Every inject whose rubric is in the ai_scorer dimensions-LIST format is
    actually scoreable by tools/ttx/ai_scorer; the 3 new penta-gd-06 TTX
    scenarios (document-integrity / slo-meltdown / grounding-red-team) are
    fully list-format and scoreable.
  * The legacy forge_ascent-era packs use the dict-of-dicts rubric shape, which
    the loader tolerates but ai_scorer.judge_response CANNOT consume — see
    ``test_legacy_dict_of_dicts_rubric_should_be_scoreable`` (xfail).

LATENT BUG (documented, not fixed here — belongs to a follow-up):
  ``ai_scorer.judge_response`` reads ``rubric["dimensions"]`` and iterates it.
  For the legacy dict-of-dicts packs (forge_ascent, hunt_the_fleet, meridian)
  ``dimensions`` is a *mapping*, so iteration yields string keys and
  ``d.get("weight")`` raises ``AttributeError`` — uncaught by ``score_response``,
  so ``POST /api/gameday/response`` would 500 for those scenarios. This module
  locks the incompatibility as an xfail so the fix (tolerate or fail-loud on the
  legacy shape) flips it green.
"""

from __future__ import annotations

import importlib

import pytest

from tools.ttx.ai_scorer import _weighted_total
from tools.ttx.scenario_loader import list_scenario_slugs, load_scenario

# The 3 new platform-generation TTX scenarios (penta-gd-06 / PR #546).
_NEW_TTX_SLUGS = ("document-integrity", "slo-meltdown", "grounding-red-team")
# Legacy forge_ascent-era packs — dict-of-dicts rubrics.
_LEGACY_DOD_SLUGS = ("forge_ascent", "hunt_the_fleet", "meridian")

_COMMON_KEYS = {"id", "name", "description"}
_AIOPS_BRIEFS = {"red_brief", "blue_brief", "gold_brief", "green_brief"}
_CYBER_BRIEFS = {"attack_brief", "defense_brief", "innovation_brief", "compliance_brief"}


# ---------------------------------------------------------------------------
# ALL_SCENARIO_PACKS — full schema coverage
# ---------------------------------------------------------------------------

def test_all_scenario_packs_have_complete_schema():
    from tools.gameday.scenarios import ALL_SCENARIO_PACKS

    assert ALL_SCENARIO_PACKS, "no scenario packs registered"
    for pack, scenarios in ALL_SCENARIO_PACKS.items():
        assert scenarios, f"pack {pack!r} has no scenarios"
        for sc in scenarios:
            keys = set(sc)
            missing_common = _COMMON_KEYS - keys
            assert not missing_common, f"{pack}/{sc.get('id')} missing {missing_common}"
            # Must carry a complete brief set of ONE of the two known shapes.
            has_aiops = _AIOPS_BRIEFS <= keys
            has_cyber = _CYBER_BRIEFS <= keys
            assert has_aiops or has_cyber, (
                f"{pack}/{sc.get('id')} has neither a complete AI-Ops brief set "
                f"{_AIOPS_BRIEFS} nor cyber brief set {_CYBER_BRIEFS}"
            )
            brief_set = _AIOPS_BRIEFS if has_aiops else _CYBER_BRIEFS
            for key in _COMMON_KEYS | brief_set:
                assert str(sc[key]).strip(), f"{pack}/{sc.get('id')} empty {key}"


def test_scenario_ids_unique_across_all_packs():
    from tools.gameday.scenarios import ALL_SCENARIO_PACKS

    seen: dict[str, str] = {}
    for pack, scenarios in ALL_SCENARIO_PACKS.items():
        for sc in scenarios:
            sid = sc["id"]
            assert sid not in seen, f"duplicate id {sid!r} in {pack!r} and {seen[sid]!r}"
            seen[sid] = pack


# ---------------------------------------------------------------------------
# AI_TOOLS_CATALOG — mcp_tool entries resolve to well-formed registry entries
# ---------------------------------------------------------------------------

def test_catalog_mcp_tools_resolve_to_wellformed_registry_entries():
    from apps.ai_gameday.constants import AI_TOOLS_CATALOG
    from tools.mcp.tool_registry import TOOL_REGISTRY

    checked = 0
    for entry in AI_TOOLS_CATALOG:
        mcp_tool = entry.get("mcp_tool")
        if not mcp_tool:
            continue
        checked += 1
        assert mcp_tool in TOOL_REGISTRY, (
            f"catalog tool {mcp_tool!r} (slug {entry.get('slug')!r}) not in TOOL_REGISTRY"
        )
        spec = TOOL_REGISTRY[mcp_tool]
        assert isinstance(spec, dict), f"{mcp_tool!r} registry entry is not a dict"
        assert spec.get("module"), f"{mcp_tool!r} registry entry missing module"
        assert spec.get("handler"), f"{mcp_tool!r} registry entry missing handler"
    assert checked >= 10, f"expected >=10 mcp-backed catalog entries, found {checked}"


# ---------------------------------------------------------------------------
# scenario_loader — every slug loads (tolerance across legacy + new)
# ---------------------------------------------------------------------------

def test_scenario_slugs_discovered():
    slugs = set(list_scenario_slugs())
    for slug in _NEW_TTX_SLUGS + _LEGACY_DOD_SLUGS:
        assert slug in slugs, f"{slug!r} not discovered on disk"


@pytest.mark.parametrize("slug", list_scenario_slugs())
def test_every_scenario_slug_loads(slug):
    """Loader tolerance: every pack on disk loads and satisfies the required
    top-level contract (name/session_mode/injects), legacy or new."""
    sc = load_scenario(slug)
    assert sc["name"]
    assert sc["session_mode"] in ("live", "async")
    assert sc["injects"]
    for inj in sc["injects"]:
        assert inj.get("id") and inj.get("title")


def _list_rubric_injects(sc):
    """Yield (inject, rubric) for injects whose rubric is the ai_scorer
    dimensions-LIST format."""
    for inj in sc.get("injects", []):
        rub = inj.get("scoring", {}).get("rubric")
        if isinstance(rub, dict) and isinstance(rub.get("dimensions"), list):
            yield inj, rub


@pytest.mark.parametrize("slug", list_scenario_slugs())
def test_list_format_rubrics_are_scoreable(slug):
    """Every dimensions-LIST rubric across all packs is consumable by ai_scorer:
    dims are well-formed and _weighted_total yields a bounded 0-100 score."""
    sc = load_scenario(slug)
    for inj, rub in _list_rubric_injects(sc):
        dims = rub["dimensions"]
        assert dims, f"{slug}/{inj['id']} empty dimensions list"
        for d in dims:
            assert {"id", "weight", "prompt"} <= set(d), (
                f"{slug}/{inj['id']} dim missing id/weight/prompt: {d}"
            )
        assert sum(d["weight"] for d in dims) > 0, f"{slug}/{inj['id']} zero total weight"
        wt = _weighted_total({d["id"]: 9 for d in dims}, dims)
        assert 0 <= wt <= 100, f"{slug}/{inj['id']} weighted total out of range: {wt}"


@pytest.mark.parametrize("slug", _NEW_TTX_SLUGS)
def test_new_ttx_scenarios_are_fully_scoreable(slug):
    """The 3 new penta-gd-06 scenarios must be 100% list-format and scoreable."""
    sc = load_scenario(slug)
    injects = sc["injects"]
    assert injects
    scoreable = list(_list_rubric_injects(sc))
    assert len(scoreable) == len(injects), (
        f"{slug}: {len(injects) - len(scoreable)} inject(s) not in list-rubric format"
    )
    for inj, rub in scoreable:
        wt = _weighted_total({d["id"]: 8 for d in rub["dimensions"]}, rub["dimensions"])
        assert 0 <= wt <= 100


# ---------------------------------------------------------------------------
# Legacy dict-of-dicts rubrics — tolerated by loader, NOT scoreable (latent bug)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug", _LEGACY_DOD_SLUGS)
def test_legacy_packs_use_dict_of_dicts_rubrics(slug):
    """The forge_ascent-era packs load fine but expose the legacy dict-of-dicts
    rubric shape (dimensions is a mapping, not a list) — i.e. NOT the ai_scorer
    format. This asserts loader tolerance and pins the format for the xfail
    below."""
    sc = load_scenario(slug)
    saw_dod = False
    for inj in sc["injects"]:
        rub = inj.get("scoring", {}).get("rubric")
        if isinstance(rub, dict) and isinstance(rub.get("dimensions"), dict):
            saw_dod = True
    assert saw_dod, f"{slug}: expected at least one legacy dict-of-dicts rubric"


@pytest.mark.xfail(
    reason=(
        "LATENT BUG: ai_scorer.judge_response cannot consume legacy dict-of-dicts "
        "rubrics — dimensions is a mapping, so iterating yields str keys and "
        "d.get('weight') raises AttributeError (uncaught by score_response, so "
        "POST /api/gameday/response 500s for forge_ascent/hunt_the_fleet/meridian). "
        "Follow-up: tolerate the legacy shape or mark it fail-loud unscored."
    ),
    strict=False,
    raises=AttributeError,
)
def test_legacy_dict_of_dicts_rubric_should_be_scoreable(monkeypatch):
    """Desired behaviour: a legacy dict-of-dicts rubric is either scored or
    fail-loud unscored — never an unhandled crash. Currently raises."""
    router_mod = importlib.import_module("tools.llm.router")

    class _R:
        def __init__(self, *a, **k):
            pass

        def has_any_llm(self):
            return True

        def invoke(self, function, req):
            class _X:
                content = '{"scores": {}, "rationale": "x", "confidence": 0.5}'

            return _X()

    class _Req:
        def __init__(self, **k):
            pass

    monkeypatch.setattr(router_mod, "LLMRouter", _R)
    monkeypatch.setattr(router_mod, "LLMRequest", _Req)

    from tools.ttx import ai_scorer

    legacy_rubric = {
        "dimensions": {
            "clarity": {"weight": 0.5, "prompt": "clarity"},
            "depth": {"weight": 0.5, "prompt": "depth"},
        }
    }
    out = ai_scorer.judge_response("inject", "response", legacy_rubric)
    # If the bug is fixed this must hold (score or explicit unscored, not a crash):
    assert "total" in out
