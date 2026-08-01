# CUI // SP-CTI
"""Two DIC surfaces were shipped as `ready: True` while backed by nothing.

/snippets   — _SNIPPETS was referenced exactly once, to render. Five descriptions
              of UI widgets you could not do anything with. Its own IQE seed query
              pointed at `dic.snippets`, a collection the IQE adapter never
              registered.
/finetune   — a static 3-provider list. Its template shipped a DISABLED button
              titled "Wiring lands in dic-finetune-01" and "No training jobs yet",
              while a REAL finetune canvas exists at /finetune with datasets,
              jobs, models and tables.

The risk in this change is deleting the wrong thing, so most of these tests guard
what must SURVIVE.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py"
TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"


class TestRemoved:
    def test_snippets_template_is_gone(self):
        assert not (TEMPLATES / "snippets.html").exists()

    def test_finetune_template_is_gone(self):
        assert not (TEMPLATES / "finetune.html").exists()

    def test_mirrored_templates_are_gone_too(self):
        mirror = REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates" / "document_intelligence"
        assert not (mirror / "snippets.html").exists()
        assert not (mirror / "finetune.html").exists()

    def test_no_dangling_constants_or_routes(self):
        src = BLUEPRINT.read_text(encoding="utf-8")
        for dead in ("_SNIPPETS", "_LOCAL_PROVIDERS", "snippets.html", "finetune.html"):
            assert dead not in src, f"{dead} still referenced"

    def test_routes_are_gone(self):
        src = BLUEPRINT.read_text(encoding="utf-8")
        assert '@dic_bp.route("/snippets")' not in src
        assert '@dic_bp.route("/finetune")' not in src

    def test_tiles_are_gone(self):
        from tools.document_intelligence.blueprint import _PAGES
        names = {p["name"] for p in _PAGES}
        assert "Snippets" not in names
        assert "Air-Gap Fine-Tuning" not in names


class TestSurvived:
    """The point of the corrections: /templates and /analytics are REAL."""

    def test_templates_page_survives(self):
        """_TEMPLATES feeds api_template_instantiate, which creates real
        dic_documents + sections, and backs Tech Writer and AI-Assist."""
        from tools.document_intelligence.blueprint import _PAGES, _TEMPLATES
        assert _TEMPLATES, "templates are the source for real document creation"
        assert "Templates" in {p["name"] for p in _PAGES}
        assert '@dic_bp.route("/templates")' in BLUEPRINT.read_text(encoding="utf-8")
        assert (TEMPLATES / "templates.html").exists()

    def test_analytics_page_survives(self):
        """analytics_engine is real (12+ functions). It looked empty because the
        KG was, not because the page was fake."""
        from tools.document_intelligence.blueprint import _PAGES
        assert "Analytics" in {p["name"] for p in _PAGES}
        assert (TEMPLATES / "analytics.html").exists()

    def test_the_real_finetune_canvas_is_untouched(self):
        """DIC's /finetune was a stub duplicate; the actual canvas stays."""
        import yaml
        reg = yaml.safe_load(
            (REPO_ROOT / "args" / "component_registry.yaml").read_text(encoding="utf-8"))
        keys = {c.get("key") for c in reg.get("components", [])}
        assert "finetune" in keys, "the real finetune canvas must survive"


class TestPageGroups:
    def test_no_group_names_a_removed_tile(self):
        """A stale name would render an empty slot."""
        from tools.document_intelligence.blueprint import _PAGE_GROUPS, _PAGES
        known = {p["name"] for p in _PAGES}
        for title, _desc, names in _PAGE_GROUPS:
            for n in names:
                assert n in known, f"group {title!r} names removed tile {n!r}"

    def test_no_empty_groups(self):
        """'5 · Advanced' held only Air-Gap Fine-Tuning; it must not survive as an
        empty heading."""
        from tools.document_intelligence.blueprint import _PAGE_GROUPS
        for title, _desc, names in _PAGE_GROUPS:
            assert names, f"group {title!r} is empty"

    def test_every_tile_still_reachable_from_a_group(self):
        """_grouped_pages appends strays to a trailing group rather than dropping
        them, but a tile in no group is still a nav gap."""
        from tools.document_intelligence.blueprint import _PAGE_GROUPS, _PAGES
        grouped = {n for _t, _d, names in _PAGE_GROUPS for n in names}
        for p in _PAGES:
            assert p["name"] in grouped, f"tile {p['name']!r} is in no group"
