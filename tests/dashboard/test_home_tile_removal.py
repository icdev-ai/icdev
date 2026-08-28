# CUI // SP-CTI
"""The two LLM-cache monitor cards are gone from Home, WHOLE (cch-obs-08).

WHY THEY WENT. Home carried "LLM Prompt Cache" and "Cortex Governance" side by side.
Both say "cache", they measure DIFFERENT things -- one is the response cache (was an LLM
call avoided entirely), the other the governed facade's own cache -- and each tile owned a
help-icon sentence trying to say so ("Distinct from the Cortex facade response cache" /
"Not the LLM prompt cache"). When a surface needs to explain what it is NOT, in a tooltip,
it is in the wrong place. Both read zero for reasons a 140px tile has no room to give.

WHAT DID NOT GO. /cache-savings and /cortex/ keep their pages, their APIs and their nav
entries. This removes a Home SUMMARY, not a capability -- asserted below, because deleting
the only link to a page is how a page becomes unreachable while every test still passes.

THE FAILURE MODE THIS PINS is a HALF re-add, and it is silent both ways: markup with no
renderer draws a permanent "Loading...", and a renderer with no markup polls an endpoint
on every page load and writes into a div that does not exist. Neither raises.
"""
from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "tools" / "dashboard" / "templates" / "index.html"
MIRROR = REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates" / "index.html"
BASE = REPO_ROOT / "tools" / "dashboard" / "templates" / "base.html"

# Every token either tile owned: the div ids, the renderers, the window handles the
# refresh loop called, and the two endpoints only these tiles ever fetched.
REMOVED_TOKENS = [
    'id="tile-cache"',
    'id="tile-cortex"',
    "renderCacheTile",
    "renderCortexTile",
    "/api/cache-savings/tile",
    "/cortex/api/metrics/tile",
]


@pytest.mark.parametrize("path", [INDEX, MIRROR], ids=["tools", "icdev-mirror"])
@pytest.mark.parametrize("token", REMOVED_TOKENS)
def test_no_trace_of_either_tile_remains(path, token):
    assert token not in path.read_text(encoding="utf-8"), (
        f"{token} is back in {path.name}. If the tile is being restored it must be "
        "restored WHOLE -- markup, renderer, and refresh-loop call -- because any two "
        "of the three without the third fail silently."
    )


@pytest.mark.parametrize("label", ["LLM Prompt Cache", "Cortex Governance"])
def test_the_card_labels_are_gone(label):
    assert label not in INDEX.read_text(encoding="utf-8")


def test_the_mirror_matches_byte_for_byte():
    """A tile removed in one tree and left in the other is the same defect, half-shipped."""
    assert INDEX.read_bytes() == MIRROR.read_bytes()


# ---------------------------------------------------------------------------
# what must SURVIVE -- this removed a summary, not a capability
# ---------------------------------------------------------------------------


def test_cache_savings_is_still_reachable_from_nav():
    """The Home tile carried a "View dashboard" link. Nav is the remaining route, and
    without it /cache-savings becomes a page nothing links to (page gate, point 7)."""
    assert 'href="/cache-savings"' in BASE.read_text(encoding="utf-8")


def test_cortex_is_still_reachable_from_the_registry():
    import yaml

    reg = yaml.safe_load(
        (REPO_ROOT / "args" / "component_registry.yaml").read_text(encoding="utf-8")
    )
    comps = reg.get("components") or reg.get("canvases") or []
    if isinstance(comps, dict):
        comps = list(comps.values())
    cortex = next(c for c in comps if isinstance(c, dict) and c.get("key") == "cortex")
    hrefs = [lnk.get("href") for lnk in ((cortex.get("nav") or {}).get("links") or [])]
    assert "/cortex/" in hrefs


def test_the_backing_endpoints_are_untouched():
    """The tiles were removed, NOT their APIs -- /cache-savings and /cortex still read
    them, and deleting a route because one of its callers went away is a different
    change with a different blast radius."""
    bp = (REPO_ROOT / "tools" / "cache_savings" / "blueprint.py").read_text(encoding="utf-8")
    assert "/api/cache-savings/tile" in bp
    assert "/api/cache-savings/stats" in bp


def test_the_remaining_monitor_tiles_are_intact():
    """A cut that took a neighbour with it would otherwise pass every assertion above."""
    html = INDEX.read_text(encoding="utf-8")
    for still_there in ("MCP Monitor", "Oracle Insights", "Agent Health", "Task Board Status"):
        assert still_there in html, f"{still_there} was removed by accident"
    for handle in ("renderMcpTile", "renderOracleTile", "renderAceMonitorTile"):
        assert handle in html, f"{handle} lost its refresh-loop wiring"


def test_the_template_still_parses():
    import jinja2

    jinja2.Environment().parse(INDEX.read_text(encoding="utf-8"))
