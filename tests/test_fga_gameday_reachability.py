# CUI // SP-CTI
"""GameDay reachability: an orphaned feature and 12 links that cannot work.

The scenario manager and builder both exist and are asserted live by
tests/test_penta_gd_routes.py, but the hub linked neither — the builder is
linked only from the manager, which was itself orphaned, so the whole feature
was reachable only by typing the URL.

Separately, the player console rendered all 34 AI-tool catalog entries as GET
anchors; 12 are POST-only /api endpoints, so a click opened a tab that 405s.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "ai_gameday"


# ---------------------------------------------------------------------------
# fga-gd-01 — the orphaned scenario manager and builder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("route", ["/gameday/scenarios", "/gameday/scenarios/builder"])
def test_hub_links_the_scenario_routes(route):
    src = (TEMPLATES / "hub.html").read_text(encoding="utf-8")
    assert route in src, f"{route} is reachable only by typing the URL"


def test_linked_routes_actually_exist():
    """A nav link to a route that does not exist is worse than none."""
    src = (REPO_ROOT / "apps" / "ai_gameday" / "blueprint.py").read_text(encoding="utf-8")
    assert '@bp.route("/gameday/scenarios")' in src
    assert '@bp.route("/gameday/scenarios/builder")' in src


# ---------------------------------------------------------------------------
# fga-gd-02 — POST-only endpoints rendered as GET anchors
# ---------------------------------------------------------------------------

def test_post_only_tools_are_not_navigable():
    from apps.ai_gameday.constants import catalog_for_render

    catalog = catalog_for_render()
    assert len(catalog) == 34
    non_nav = [t for t in catalog if not t["navigable"]]
    assert len(non_nav) == 12, "the 12 POST-only /api entries must not be links"
    assert all(t["endpoint"].startswith("/api/") for t in non_nav)


def test_every_navigable_tool_is_a_page_path():
    from apps.ai_gameday.constants import catalog_for_render

    for tool in catalog_for_render():
        if tool["navigable"]:
            assert not tool["endpoint"].startswith("/api/")


def test_player_console_branches_on_navigability():
    src = (TEMPLATES / "player.html").read_text(encoding="utf-8")
    assert "{% if tool.navigable %}" in src, "all 34 entries were rendered as anchors"
    assert 'href="{{ tool.endpoint }}"' in src, "navigable tools must still link"


def test_non_navigable_tools_still_show_their_endpoint():
    """A player can call the API from their own tooling; don't hide it."""
    src = (TEMPLATES / "player.html").read_text(encoding="utf-8")
    assert "POST {{ tool.endpoint }}" in src


def test_catalog_entries_keep_their_original_fields():
    from apps.ai_gameday.constants import AI_TOOLS_CATALOG, catalog_for_render

    rendered = {t["slug"]: t for t in catalog_for_render()}
    for original in AI_TOOLS_CATALOG:
        entry = rendered[original["slug"]]
        for key, value in original.items():
            assert entry[key] == value, f"{key} was altered for {original['slug']}"


@pytest.mark.parametrize("template", ["hub.html", "player.html"])
def test_touched_templates_parse(template):
    import jinja2

    jinja2.Environment().parse((TEMPLATES / template).read_text(encoding="utf-8"))
