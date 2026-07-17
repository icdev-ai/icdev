# CUI // SP-CTI
"""DIC had two navigation systems that disagreed.

The sidebar (args/component_registry.yaml) was a flat list; the index tiles
(_PAGES + _PAGE_GROUPS in blueprint.py) were grouped by workflow. They disagreed
on membership — Templates had a tile but no sidebar entry — and on order.

The index already knew the problem; _PAGE_GROUPS' own comment says "14
undifferentiated sibling tiles give no clue what to do first". The sidebar
ignored that grouping entirely.

These tests pin the two together so they cannot drift apart again.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "args" / "component_registry.yaml"
BLUEPRINT = REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py"

# The sidebar's only entry with no tile: the canvas index itself.
_INDEX_SLUG = "document-intelligence"


def _slug(href: str) -> str:
    return href.rstrip("/").split("/")[-1] or _INDEX_SLUG


@pytest.fixture(scope="module")
def sidebar() -> list[dict]:
    reg = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    dic = next(c for c in reg["components"] if c.get("key") == "dic")
    return dic["nav"]["links"]


@pytest.fixture(scope="module")
def tiles() -> list[tuple[str, str]]:
    src = BLUEPRINT.read_text(encoding="utf-8")
    return re.findall(r'\{"name": "([^"]+)".*?"href": "([^"]+)"', src)


class TestMembership:
    def test_every_tile_has_a_sidebar_entry(self, sidebar, tiles):
        """Templates was reachable from the index only."""
        side = {_slug(l["href"]) for l in sidebar}
        for name, href in tiles:
            assert _slug(href) in side, f"tile {name!r} has no sidebar entry"

    def test_every_sidebar_entry_has_a_tile_except_the_index(self, sidebar, tiles):
        tile_slugs = {_slug(h) for _n, h in tiles}
        for link in sidebar:
            s = _slug(link["href"])
            if s == _INDEX_SLUG:
                continue  # the index has no tile for itself, by design
            assert s in tile_slugs, f"sidebar links {link['label']!r} with no tile"

    def test_no_sidebar_link_points_at_a_deleted_page(self, sidebar):
        """A dead nav link is worse than a missing one."""
        src = BLUEPRINT.read_text(encoding="utf-8")
        for link in sidebar:
            s = _slug(link["href"])
            if s == _INDEX_SLUG:
                continue
            assert f'@dic_bp.route("/{s}")' in src, f"{link['label']!r} -> /{s} has no route"


class TestOrdering:
    def test_sidebar_follows_the_page_groups(self, sidebar):
        """Both navs order by the same four jobs: get documents in, ask
        questions, author, govern."""
        from tools.document_intelligence.blueprint import _PAGE_GROUPS, _PAGES

        href_by_name = {p["name"]: _slug(p["href"]) for p in _PAGES}
        expected = [href_by_name[n] for _t, _d, names in _PAGE_GROUPS for n in names]
        actual = [_slug(l["href"]) for l in sidebar if _slug(l["href"]) != _INDEX_SLUG]
        assert actual == expected, (
            "sidebar order must match _PAGE_GROUPS\n"
            f"  sidebar : {actual}\n  groups  : {expected}"
        )

    def test_overview_stays_first(self, sidebar):
        assert _slug(sidebar[0]["href"]) == _INDEX_SLUG
