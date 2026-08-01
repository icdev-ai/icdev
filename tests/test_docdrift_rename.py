# CUI // SP-CTI
"""ACOIC -> DocDrift: the user-facing rename, and what deliberately did NOT move.

"ACOIC" (Autonomous Compliance-Of-Impact Coupler) described a network-only
bridge. It is now fed by every docmod pack — network, crypto, software, policy,
approved changes, cited evidence — so the name was wrong in a way that mattered:
it told users the page was about infrastructure.

The module file and tables keep the legacy name on purpose. These tests pin both
halves so neither is "tidied" later by mistake.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_BP = REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py"
_TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"


class TestRoutesMoved:
    def test_page_route_is_docdrift(self):
        src = _BP.read_text(encoding="utf-8")
        assert '@dic_bp.route("/docdrift")' in src
        assert '@dic_bp.route("/api/docdrift/drift-check", methods=["POST"])' in src

    def test_old_route_redirects_rather_than_404s(self):
        """The old path is in bookmarks, kanban cards and docs — a 404 would read
        as 'the feature is gone' rather than 'it was renamed'."""
        src = _BP.read_text(encoding="utf-8")
        assert '@dic_bp.route("/acoic")' in src
        assert "def acoic_legacy_redirect" in src
        assert "code=301" in src

    def test_redirect_helpers_are_imported(self):
        """redirect/url_for were NOT in the flask import line before this change —
        the redirect would have been a 500."""
        from tools.document_intelligence import blueprint as bp

        assert hasattr(bp, "redirect") and hasattr(bp, "url_for")


class TestTemplateMoved:
    def test_template_renamed_and_route_renders_it(self):
        assert (_TEMPLATES / "docdrift.html").exists()
        assert not (_TEMPLATES / "acoic.html").exists()
        assert 'render_template(\n        "document_intelligence/docdrift.html"' in _BP.read_text(encoding="utf-8")

    def test_element_ids_and_fetch_url_moved_together(self):
        """The JS looks these up by id and posts to the API — if the ids and the
        URL don't move with the markup, the page silently stops working."""
        html = (_TEMPLATES / "docdrift.html").read_text(encoding="utf-8")
        for el in ("docdrift-topo", "docdrift-save-baseline", "docdrift-run-drift", "docdrift-status"):
            assert html.count(el) >= 2, f"{el} must appear in both markup and JS"
        assert "/document-intelligence/api/docdrift/drift-check" in html
        assert "api/acoic/drift-check" not in html

    def test_no_acoic_left_in_user_facing_copy(self):
        for name in ("docdrift.html", "index.html", "templates.html", "review.html"):
            text = (_TEMPLATES / name).read_text(encoding="utf-8")
            assert "ACOIC" not in text, f"{name} still says ACOIC"


class TestNavAndTiles:
    def test_tile_and_nav_point_at_the_new_route(self):
        src = _BP.read_text(encoding="utf-8")
        assert '"name": "DocDrift"' in src
        assert '"href": "/document-intelligence/docdrift"' in src

        registry = (REPO_ROOT / "args" / "component_registry.yaml").read_text(encoding="utf-8")
        assert "href: /document-intelligence/docdrift" in registry
        assert "href: /document-intelligence/acoic" not in registry

    def test_tile_group_matches_the_renamed_tile(self):
        """_PAGE_GROUPS references tiles BY NAME — a stale name silently drops the
        tile out of its group."""
        from tools.document_intelligence.blueprint import _PAGES, _grouped_pages

        grouped = [p["name"] for g in _grouped_pages() for p in g["pages"]]
        assert "DocDrift" in grouped
        assert sorted(grouped) == sorted(p["name"] for p in _PAGES)


class TestDeliberatelyNotRenamed:
    def test_module_path_is_unchanged(self):
        """Renaming the module would churn imports across drift_detector,
        ndc_topology_drift, drift_bridge and the DIC blueprint for a string no
        user sees."""
        from tools.document_intelligence import acoic

        assert hasattr(acoic, "handle_drift")

    def test_tables_keep_their_names(self):
        """_ensure_schema uses CREATE TABLE IF NOT EXISTS: a missed call site
        after a table rename would silently recreate the old table alongside the
        new one and split the data rather than fail loudly."""
        src = (REPO_ROOT / "tools" / "document_intelligence" / "acoic.py").read_text(encoding="utf-8")
        assert "dic_acoic_regen_queue" in src
        assert "dic_drift_events" in src

    def test_template_catalog_id_is_stable(self):
        """_TEMPLATES[*]['id'] is a data key behind /api/templates/<id>/instantiate,
        exercised by features/dic_document_intelligence.feature and e2e_full.py."""
        from tools.document_intelligence.blueprint import _TEMPLATES

        entry = next(t for t in _TEMPLATES if t["id"] == "acoic")
        assert entry["name"] == "DocDrift"       # renamed for humans
        assert entry["id"] == "acoic"            # kept for the contract

    def test_the_rename_is_explained_where_someone_would_look(self):
        src = (REPO_ROOT / "tools" / "document_intelligence" / "acoic.py").read_text(encoding="utf-8")
        assert "DocDrift" in src and "on purpose" in src
