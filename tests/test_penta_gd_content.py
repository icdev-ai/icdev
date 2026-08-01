# CUI // SP-CTI
"""penta-gd-05 — GameDay CONTENT freshness regression tests.

Covers:
  * The 6 new current-platform AI League packs (Cortex Gauntlet, DIC Document
    Sprint, NDC Topology Challenge, Observability Fire Drill, Foundry 0->1 Race,
    GraphRAG Hunt) are registered in ALL_SCENARIO_PACKS and every scenario
    carries the exact AI-Ops pack schema fields.
  * AI_TOOLS_CATALOG freshness: every catalog entry that declares an `mcp_tool`
    resolves to a real key in tools/mcp/tool_registry.py::TOOL_REGISTRY. No
    invented tool names, and legacy docgen.*/pna.predict entries are gone.
  * The rescinded EO 14110 anchor has been retired from args/gameday_teams.yaml.
"""

from __future__ import annotations

import pathlib

# ── AI-Ops pack schema (mirrors the existing ace_showdown/docgen_race packs) ──
_REQUIRED_SCENARIO_KEYS = {
    "id",
    "name",
    "description",
    "red_brief",
    "blue_brief",
    "gold_brief",
    "green_brief",
}

# Packs added by penta-gd-05.
_NEW_PACKS = (
    "cortex_gauntlet",
    "dic_document_sprint",
    "ndc_topology_challenge",
    "observability_fire_drill",
    "foundry_zero_to_one",
    "graphrag_hunt",
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_new_packs_registered_in_all_scenario_packs():
    from tools.gameday.scenarios import ALL_SCENARIO_PACKS

    for pack in _NEW_PACKS:
        assert pack in ALL_SCENARIO_PACKS, f"pack {pack!r} not registered"
        assert ALL_SCENARIO_PACKS[pack], f"pack {pack!r} has no scenarios"


def test_new_packs_load_via_scenario_listing():
    """Every new pack is enumerable and its scenarios carry the exact schema."""
    from tools.gameday.scenarios import get_pack_metadata, list_scenarios

    meta = {m["pack"]: m for m in get_pack_metadata()}
    for pack in _NEW_PACKS:
        assert pack in meta, f"{pack!r} missing from pack metadata"
        scenarios = list_scenarios(pack)
        assert scenarios, f"{pack!r} listed no scenarios"
        for sc in scenarios:
            missing = _REQUIRED_SCENARIO_KEYS - set(sc)
            assert not missing, f"{pack}/{sc.get('id')} missing fields: {missing}"
            for key in _REQUIRED_SCENARIO_KEYS:
                assert str(sc[key]).strip(), f"{pack}/{sc.get('id')} empty {key}"


def test_new_pack_scenario_ids_unique_across_all_packs():
    from tools.gameday.scenarios import ALL_SCENARIO_PACKS

    seen: dict[str, str] = {}
    for pack, scenarios in ALL_SCENARIO_PACKS.items():
        for sc in scenarios:
            sid = sc["id"]
            assert sid not in seen, (
                f"duplicate scenario id {sid!r} in {pack!r} and {seen[sid]!r}"
            )
            seen[sid] = pack


def test_ai_tools_catalog_mcp_tools_resolve():
    """Every AI_TOOLS_CATALOG entry with an mcp_tool resolves in the registry."""
    from apps.ai_gameday.constants import AI_TOOLS_CATALOG
    from tools.mcp.tool_registry import TOOL_REGISTRY

    registry_keys = set(TOOL_REGISTRY.keys())
    checked = 0
    for entry in AI_TOOLS_CATALOG:
        mcp_tool = entry.get("mcp_tool")
        if not mcp_tool:
            continue
        checked += 1
        assert mcp_tool in registry_keys, (
            f"catalog tool {mcp_tool!r} (slug {entry.get('slug')!r}) "
            f"does not resolve in tools/mcp/tool_registry.py"
        )
    # The refresh added real MCP-backed entries; guard against silent regression.
    assert checked >= 10, f"expected >=10 mcp-backed entries, found {checked}"


def test_ai_tools_catalog_entries_render_shape():
    """Every entry keeps the frontend picker keys (endpoint/slug/icon/label)."""
    from apps.ai_gameday.constants import AI_TOOLS_CATALOG

    for entry in AI_TOOLS_CATALOG:
        for key in ("slug", "label", "endpoint", "icon"):
            assert entry.get(key), f"entry {entry.get('slug')!r} missing {key}"


def test_legacy_catalog_entries_retired():
    from apps.ai_gameday.constants import AI_TOOLS_CATALOG

    slugs = {e["slug"] for e in AI_TOOLS_CATALOG}
    for dead in ("docgen.session", "docgen.workflow", "pna.predict"):
        assert dead not in slugs, f"legacy catalog slug {dead!r} still present"


def test_new_capability_canvases_referenced():
    """The refresh must surface Cortex / DIC / NDC / Observability / Foundry."""
    from apps.ai_gameday.constants import AI_TOOLS_CATALOG

    mcp_tools = {e.get("mcp_tool") for e in AI_TOOLS_CATALOG if e.get("mcp_tool")}
    for expected in (
        "cortex_ask",
        "dic_ingest",
        "dic_generate",
        "mc_net_ai_assist",
        "slo_dashboard",
        "foundry_run",
        "kg_search",
        "kanban_board_summary",
    ):
        assert expected in mcp_tools, f"expected {expected!r} in catalog mcp_tools"


def test_eo_14110_anchor_retired():
    """The rescinded EO 14110 must not remain as a compliance anchor."""
    text = (_REPO_ROOT / "args" / "gameday_teams.yaml").read_text(encoding="utf-8")
    assert "14110" not in text, "rescinded EO 14110 anchor still present"
    assert "M-25-21" in text and "M-26-04" in text, "OMB anchors missing"


def test_icdev_mirrors_in_sync():
    """The icdev/ mirror copies must match the canonical constants files."""
    pairs = [
        ("tools/gameday/constants.py", "icdev/tools/gameday/constants.py"),
        ("apps/ai_gameday/constants.py", "icdev/apps/ai_gameday/constants.py"),
    ]
    for canon, mirror in pairs:
        c = (_REPO_ROOT / canon).read_text(encoding="utf-8")
        m = (_REPO_ROOT / mirror).read_text(encoding="utf-8")
        assert c == m, f"mirror out of sync: {mirror}"
