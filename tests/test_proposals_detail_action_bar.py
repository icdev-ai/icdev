# CUI // SP-CTI
"""Tests for /proposals/<id> — GovCon Intelligence action bar present.

Verifies:
1. Template source declares the GovCon Intelligence action bar section.
2. Template source contains all five action buttons (Extract/Map/Compliance/Draft/Bid Rec).
3. Rendered HTML includes the action bar heading and all five button labels.
4. Route renders proposals/detail.html with the expected context variables.
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
_DETAIL_TEMPLATE = _TEMPLATES_DIR / "proposals" / "detail.html"

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
    bid_decision TEXT,
    estimated_value_low REAL,
    estimated_value_high REAL,
    questions_due_date TEXT,
    win_probability REAL,
    capture_phase TEXT,
    capture_notes TEXT,
    win_themes TEXT,
    key_discriminators TEXT,
    ptw_low REAL,
    ptw_high REAL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    volume_id TEXT,
    section_number TEXT,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'not_started',
    priority TEXT,
    writer TEXT,
    word_count INTEGER DEFAULT 0,
    due_date TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_volumes (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    volume_number INTEGER,
    title TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_compliance_matrix (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    section_reference TEXT,
    requirement_text TEXT,
    compliance_status TEXT DEFAULT 'not_addressed',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_reviews (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    review_type TEXT,
    scheduled_date TEXT,
    status TEXT DEFAULT 'scheduled',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_review_findings (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    severity TEXT DEFAULT 'minor',
    status TEXT DEFAULT 'open',
    description TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_questions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    question_number INTEGER,
    question_text TEXT,
    status TEXT DEFAULT 'draft',
    priority TEXT DEFAULT 'medium',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_question_responses (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    response_text TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_amendments (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    version_number INTEGER,
    title TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_reviewer_assignments (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    reviewer_email TEXT,
    role TEXT,
    created_at TEXT
);
"""

_OPP_ID = "test-opp-001"
_OPP_ROW = (
    _OPP_ID,
    None,
    "RFP-2026-001",
    "Test Opportunity",
    "DoD",
    None,
    "2026-12-31",
    "17:00",
    None,
    None,
    "FFP",
    "intake",
    None,
    None,
    None,
    None,
    None,
    None,
    "2026-01-01T00:00:00",
)


# ---------------------------------------------------------------------------
# Static: template source checks
# ---------------------------------------------------------------------------


class TestTemplateSourceContainsActionBar:
    """detail.html must declare the GovCon Intelligence action bar with all five buttons."""

    def test_govcon_intelligence_heading_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "GovCon Intelligence" in source, (
            "proposals/detail.html must contain a GovCon Intelligence heading"
        )

    def test_extract_requirements_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Extract Requirements" in source, (
            "proposals/detail.html must contain an Extract Requirements button"
        )

    def test_map_capabilities_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Map Capabilities" in source, (
            "proposals/detail.html must contain a Map Capabilities button"
        )

    def test_auto_compliance_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Auto-Compliance" in source, (
            "proposals/detail.html must contain an Auto-Compliance button"
        )

    def test_ai_draft_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "AI Draft All" in source, (
            "proposals/detail.html must contain an AI Draft All button"
        )

    def test_bid_recommendation_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Bid Recommendation" in source, (
            "proposals/detail.html must contain a Bid Recommendation button"
        )

    def test_govcon_action_handler_wired_for_extract(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('extract'" in source, (
            "Extract Requirements button must call govconAction('extract', ...)"
        )

    def test_govcon_action_handler_wired_for_map(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('map'" in source, (
            "Map Capabilities button must call govconAction('map', ...)"
        )

    def test_govcon_action_handler_wired_for_compliance(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('compliance'" in source, (
            "Auto-Compliance button must call govconAction('compliance', ...)"
        )

    def test_govcon_action_handler_wired_for_draft(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('draft'" in source, (
            "AI Draft All button must call govconAction('draft', ...)"
        )

    def test_govcon_action_handler_wired_for_bid(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('bid'" in source, (
            "Bid Recommendation button must call govconAction('bid', ...)"
        )

    def test_govcon_status_div_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="govcon-status"' in source, (
            "proposals/detail.html must contain a #govcon-status feedback element"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: action bar elements present in output
# ---------------------------------------------------------------------------


class TestRenderedHtmlContainsActionBar:
    """Jinja2-rendered proposals/detail.html must include all GovCon action bar elements."""

    _RENDER_CONTEXT = {
        "opp": {
            "id": _OPP_ID,
            "title": "Test Opportunity",
            "agency": "DoD",
            "sub_agency": None,
            "solicitation_number": "RFP-2026-001",
            "due_date": "2026-12-31",
            "proposal_type": "FFP",
            "status": "intake",
            "capture_manager": None,
            "proposal_manager": None,
            "bid_decision": None,
            "naics_code": None,
            "set_aside_type": None,
            "estimated_value_low": None,
            "estimated_value_high": None,
            "questions_due_date": None,
            "win_probability": None,
            "capture_phase": None,
            "capture_notes": None,
            "win_themes": None,
            "key_discriminators": None,
            "ptw_low": None,
            "ptw_high": None,
        },
        "sections": [],
        "volumes": [],
        "compliance_items": [],
        "reviews": [],
        "findings": [],
        "stats": {
            "sections_total": 0,
            "sections_complete": 0,
            "compliance_coverage_pct": 0,
            "open_findings": 0,
            "critical_findings": 0,
            "section_status_distribution": {},
            "finding_severity_distribution": {},
        },
        "compliance_stats": {
            "total": 0,
            "compliant": 0,
            "partial": 0,
            "non_compliant": 0,
            "not_addressed": 0,
            "not_applicable": 0,
            "gap_pct": 0,
        },
        "reviews_data": [],
        "days_left": 30,
        "questions": [],
        "question_stats": {
            "total": 0,
            "high_priority": 0,
            "draft": 0,
            "approved": 0,
            "submitted": 0,
            "answered": 0,
        },
        "questions_days_left": None,
        "amendments": [],
        "responses": {},
    }

    def _render(self) -> str:
        from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

        stub_base = (
            "{% block global_banner %}{% endblock %}"
            "{% block content %}{% endblock %}"
        )
        env = Environment(
            autoescape=True,
            loader=ChoiceLoader([
                DictLoader({"base.html": stub_base}),
                FileSystemLoader(str(_TEMPLATES_DIR)),
            ])
        )
        tmpl = env.get_template("proposals/detail.html")
        return tmpl.render(**self._RENDER_CONTEXT)

    def test_govcon_intelligence_heading_in_html(self):
        html = self._render()
        assert "GovCon Intelligence" in html, (
            "Rendered HTML must contain the GovCon Intelligence heading"
        )

    def test_extract_requirements_in_html(self):
        html = self._render()
        assert "Extract Requirements" in html, (
            "Rendered HTML must contain Extract Requirements button text"
        )

    def test_map_capabilities_in_html(self):
        html = self._render()
        assert "Map Capabilities" in html, (
            "Rendered HTML must contain Map Capabilities button text"
        )

    def test_auto_compliance_in_html(self):
        html = self._render()
        assert "Auto-Compliance" in html, (
            "Rendered HTML must contain Auto-Compliance button text"
        )

    def test_ai_draft_all_in_html(self):
        html = self._render()
        assert "AI Draft All" in html, (
            "Rendered HTML must contain AI Draft All button text"
        )

    def test_bid_recommendation_in_html(self):
        html = self._render()
        assert "Bid Recommendation" in html, (
            "Rendered HTML must contain Bid Recommendation button text"
        )

    def test_govcon_status_div_in_html(self):
        html = self._render()
        assert "govcon-status" in html, (
            "Rendered HTML must contain the govcon-status feedback element"
        )


# ---------------------------------------------------------------------------
# Route test: GET /proposals/<id> renders proposals/detail.html
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.execute(
        """INSERT INTO proposal_opportunities
           (id, solicitation_number, title, agency, due_date, proposal_type, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (_OPP_ID, "RFP-2026-001", "Test Opportunity", "DoD", "2026-12-31", "FFP", "intake", "2026-01-01T00:00:00"),
    )
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


class TestProposalsDetailRouteTemplate:
    """GET /proposals/<id> must render proposals/detail.html with the expected context."""

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
                resp = c.get(f"/proposals/{_OPP_ID}")
                captured["_status"] = resp.status_code

        return captured

    def test_route_returns_200(self, flask_app):
        result = self._call_route(flask_app)
        assert result["_status"] == 200

    def test_template_is_proposals_detail(self, flask_app):
        result = self._call_route(flask_app)
        assert result["_template"] == "proposals/detail.html"

    def test_opp_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "opp" in result, "render_template must receive opp dict"

    def test_stats_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "stats" in result, "render_template must receive stats dict"

    def test_sections_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "sections" in result, "render_template must receive sections list"

    def test_compliance_items_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "compliance_items" in result, "render_template must receive compliance_items list"

    def test_questions_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "questions" in result, "render_template must receive questions list"

    def test_amendments_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "amendments" in result, "render_template must receive amendments list"

    def test_unknown_opp_returns_404(self, flask_app):
        captured = {}

        def fake_render(template_name, **kwargs):
            captured["_template"] = template_name
            return "NOT FOUND"

        with patch("tools.dashboard.app.render_template", side_effect=fake_render):
            with flask_app.test_client() as c:
                resp = c.get("/proposals/nonexistent-id-xyz")

        assert resp.status_code == 404
        assert captured.get("_template") == "404.html"
