# CUI // SP-CTI
"""Tests for /proposals — CUI classification banner present.

Verifies:
1. Template source declares the classification_macros import.
2. Template source calls design_classification_banner with 'CUI'.
3. Rendered HTML for GET /proposals contains the banner element and CUI marking.
"""
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_LIST_TEMPLATE = _TEMPLATES_DIR / "proposals" / "list.html"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_opportunities (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    solicitation_number TEXT NOT NULL,
    title TEXT NOT NULL,
    agency TEXT NOT NULL,
    sub_agency TEXT,
    due_date TEXT NOT NULL,
    due_time TEXT DEFAULT '17:00',
    set_aside_type TEXT,
    naics_code TEXT,
    proposal_type TEXT NOT NULL DEFAULT 'FFP',
    status TEXT NOT NULL DEFAULT 'intake',
    capture_manager TEXT,
    proposal_manager TEXT,
    created_at TEXT
);
"""


# ---------------------------------------------------------------------------
# Static: template source checks
# ---------------------------------------------------------------------------


class TestTemplateSourceContainsBanner:
    """list.html must declare and call the classification banner macro."""

    def test_macro_import_present(self):
        source = _LIST_TEMPLATE.read_text(encoding="utf-8")
        assert "classification_macros.html" in source, (
            "proposals/list.html must import classification_macros.html"
        )

    def test_banner_macro_called(self):
        source = _LIST_TEMPLATE.read_text(encoding="utf-8")
        assert "design_classification_banner" in source, (
            "proposals/list.html must call design_classification_banner"
        )

    def test_cui_classification_used(self):
        source = _LIST_TEMPLATE.read_text(encoding="utf-8")
        assert "'CUI'" in source or '"CUI"' in source, (
            "proposals/list.html must pass CUI as the classification level"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: CUI banner element present in output
# ---------------------------------------------------------------------------


class TestRenderedHtmlContainsCuiBanner:
    """Jinja2-rendered proposals/list.html must include the CUI banner HTML."""

    def test_banner_element_in_html(self):
        from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

        stub_base = (
            "{% block global_banner %}{% endblock %}"
            "{% block content %}{% endblock %}"
        )
        env = Environment(
            loader=ChoiceLoader([
                DictLoader({"base.html": stub_base}),
                FileSystemLoader(str(_TEMPLATES_DIR)),
            ])
        )

        tmpl = env.get_template("proposals/list.html")
        html = tmpl.render(opportunities=[], nearest_deadline=None)

        assert "design-classification-banner" in html, (
            "Rendered HTML must contain the design-classification-banner element"
        )

    def test_cui_label_in_html(self):
        from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

        stub_base = (
            "{% block global_banner %}{% endblock %}"
            "{% block content %}{% endblock %}"
        )
        env = Environment(
            loader=ChoiceLoader([
                DictLoader({"base.html": stub_base}),
                FileSystemLoader(str(_TEMPLATES_DIR)),
            ])
        )

        tmpl = env.get_template("proposals/list.html")
        html = tmpl.render(opportunities=[], nearest_deadline=None)

        assert "CUI" in html, "Rendered HTML must contain the CUI classification label"

    def test_banner_cls_attribute_in_html(self):
        from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

        stub_base = (
            "{% block global_banner %}{% endblock %}"
            "{% block content %}{% endblock %}"
        )
        env = Environment(
            loader=ChoiceLoader([
                DictLoader({"base.html": stub_base}),
                FileSystemLoader(str(_TEMPLATES_DIR)),
            ])
        )

        tmpl = env.get_template("proposals/list.html")
        html = tmpl.render(opportunities=[], nearest_deadline=None)

        assert "cls-CUI" in html, (
            "Rendered HTML must contain the cls-CUI CSS class on the banner element"
        )


# ---------------------------------------------------------------------------
# Route test: GET /proposals uses proposals/list.html template
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _build_test_app(tmp_path: Path) -> Flask:
    db_path = _make_db(tmp_path)

    def _get_db():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    app = Flask(__name__, template_folder=str(_TEMPLATES_DIR))
    app.config["TESTING"] = True

    from tools.dashboard.app import _register_govcon_pages
    _register_govcon_pages(app, _get_db)

    return app


class TestProposalsListRouteTemplate:
    """GET /proposals must render proposals/list.html."""

    @pytest.fixture()
    def flask_app(self, tmp_path):
        return _build_test_app(tmp_path)

    def _call_route(self, flask_app) -> dict:
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_template"] = template_name
            return "OK"

        with patch("tools.dashboard.app.render_template", side_effect=fake_render):
            with flask_app.test_client() as c:
                resp = c.get("/proposals")
                captured["_status"] = resp.status_code

        return captured

    def test_route_returns_200(self, flask_app):
        result = self._call_route(flask_app)
        assert result["_status"] == 200

    def test_template_is_proposals_list(self, flask_app):
        result = self._call_route(flask_app)
        assert result["_template"] == "proposals/list.html"

    def test_opportunities_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "opportunities" in result, "render_template must receive opportunities list"

    def test_nearest_deadline_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "nearest_deadline" in result, "render_template must receive nearest_deadline"
