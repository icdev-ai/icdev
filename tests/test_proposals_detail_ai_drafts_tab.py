# CUI // SP-CTI
"""Tests for /proposals/<id> — AI Drafts tab renders with draft section content.

Verifies:
1. Template source declares the AI Drafts tab button and content div.
2. Template source contains the drafts table with all required column headers.
3. Rendered HTML for GET /proposals/<opp_id> contains the AI Drafts tab elements.
4. Route returns 200 and renders proposals/detail.html with required context.
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
    solicitation_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    sub_agency TEXT,
    due_date TEXT NOT NULL DEFAULT '',
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
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    volume_id TEXT,
    section_number TEXT,
    title TEXT,
    status TEXT DEFAULT 'not_started',
    priority TEXT DEFAULT 'standard',
    current_word_count INTEGER DEFAULT 0,
    word_limit INTEGER,
    due_date TEXT,
    writer TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_volumes (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    volume_number INTEGER,
    title TEXT
);
CREATE TABLE IF NOT EXISTS proposal_compliance_matrix (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    section_ref TEXT,
    requirement_text TEXT DEFAULT '',
    requirement_type TEXT DEFAULT 'shall',
    compliance_status TEXT DEFAULT 'not_addressed',
    section_title TEXT,
    response_summary TEXT
);
CREATE TABLE IF NOT EXISTS proposal_reviews (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    review_type TEXT,
    scheduled_date TEXT
);
CREATE TABLE IF NOT EXISTS proposal_review_findings (
    id TEXT PRIMARY KEY,
    review_id TEXT,
    section_title TEXT,
    finding_type TEXT,
    severity TEXT DEFAULT 'minor',
    description TEXT DEFAULT '',
    assigned_to TEXT,
    status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS proposal_questions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    question_number INTEGER,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS proposal_amendments (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    version_number INTEGER
);
CREATE TABLE IF NOT EXISTS proposal_question_responses (
    id TEXT PRIMARY KEY,
    question_id TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    section_id TEXT,
    opportunity_id TEXT NOT NULL,
    draft_content TEXT NOT NULL DEFAULT '',
    draft_method TEXT,
    confidence_score REAL DEFAULT 0.0,
    domain_category TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);
"""

_OPP_ID = "opp-drafts-test-01"


# ---------------------------------------------------------------------------
# Static: template source checks
# ---------------------------------------------------------------------------


class TestTemplateSourceAiDraftsTab:
    """detail.html must declare the AI Drafts tab and its content elements."""

    def test_ai_drafts_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'data-tab="tab-drafts"' in source, (
            "proposals/detail.html must have a tab button targeting tab-drafts"
        )

    def test_ai_drafts_tab_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "AI Drafts" in source, (
            "proposals/detail.html must contain 'AI Drafts' tab label"
        )

    def test_tab_drafts_content_div_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-drafts"' in source, (
            "proposals/detail.html must have a content div with id='tab-drafts'"
        )

    def test_generate_ai_drafts_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Generate AI Drafts" in source, (
            "proposals/detail.html must contain 'Generate AI Drafts' button"
        )

    def test_govcon_draft_action_wired(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('draft'" in source, (
            "proposals/detail.html must call govconAction('draft', ...) for draft generation"
        )

    def test_drafts_table_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="drafts-table"' in source, (
            "proposals/detail.html must have a table with id='drafts-table'"
        )

    def test_drafts_tbody_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="drafts-tbody"' in source, (
            "proposals/detail.html must have tbody with id='drafts-tbody' for dynamic injection"
        )

    def test_drafts_loading_indicator_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="drafts-loading"' in source, (
            "proposals/detail.html must have a loading indicator with id='drafts-loading'"
        )

    def test_drafts_table_header_requirement(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Requirement</th>" in source, (
            "drafts table must have 'Requirement' column header"
        )

    def test_drafts_table_header_domain(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Domain</th>" in source, (
            "drafts table must have 'Domain' column header"
        )

    def test_drafts_table_header_confidence(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Confidence</th>" in source, (
            "drafts table must have 'Confidence' column header"
        )

    def test_drafts_table_header_method(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Method</th>" in source, (
            "drafts table must have 'Method' column header"
        )

    def test_drafts_table_header_status(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Status</th>" in source, (
            "drafts table must have 'Status' column header"
        )

    def test_drafts_table_header_actions(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Actions</th>" in source, (
            "drafts table must have 'Actions' column header"
        )

    def test_two_tier_llm_description_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Two-tier LLM" in source, (
            "AI Drafts tab must describe the two-tier LLM approach"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: AI Drafts tab elements present in output
# ---------------------------------------------------------------------------


def _make_stub_opp():
    return {
        "id": _OPP_ID,
        "title": "Test RFP",
        "solicitation_number": "FA8650-26-R-0001",
        "agency": "USAF",
        "sub_agency": None,
        "due_date": "2026-06-30",
        "due_time": "17:00",
        "status": "writing",
        "proposal_type": "FFP",
        "set_aside_type": None,
        "naics_code": "541512",
        "estimated_value_low": None,
        "estimated_value_high": None,
        "capture_manager": None,
        "proposal_manager": None,
        "bid_decision": None,
        "questions_due_date": None,
        "win_probability": None,
    }


def _render_detail_template(opp=None):
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

    stub_base = (
        "{% block title %}{% endblock %}"
        "{% block content %}{% endblock %}"
    )
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": stub_base}),
            FileSystemLoader(str(_TEMPLATES_DIR)),
        ]),
        autoescape=select_autoescape(["html"]),
    )

    tmpl = env.get_template("proposals/detail.html")
    return tmpl.render(
        opp=opp or _make_stub_opp(),
        sections=[],
        volumes=[],
        compliance_items=[],
        reviews=[],
        findings=[],
        stats={
            "sections_total": 0,
            "sections_complete": 0,
            "compliance_coverage_pct": 0,
            "open_findings": 0,
            "critical_findings": 0,
            "section_status_distribution": {},
            "finding_severity_distribution": {},
        },
        compliance_stats={
            "total": 0,
            "compliant": 0,
            "partial": 0,
            "non_compliant": 0,
            "not_addressed": 0,
            "not_applicable": 0,
            "gap_pct": 0,
        },
        reviews_data=[],
        days_left=40,
        questions=[],
        question_stats={
            "total": 0,
            "high_priority": 0,
            "draft": 0,
            "approved": 0,
            "submitted": 0,
            "answered": 0,
        },
        questions_days_left=None,
        amendments=[],
        responses={},
    )


class TestRenderedHtmlAiDraftsTab:
    """Jinja2-rendered proposals/detail.html must include all AI Drafts tab elements."""

    def test_tab_button_in_html(self):
        html = _render_detail_template()
        assert "tab-drafts" in html, (
            "Rendered HTML must contain the AI Drafts tab reference"
        )

    def test_ai_drafts_label_in_html(self):
        html = _render_detail_template()
        assert "AI Drafts" in html, (
            "Rendered HTML must contain the 'AI Drafts' tab label"
        )

    def test_tab_content_div_in_html(self):
        html = _render_detail_template()
        assert 'id="tab-drafts"' in html, (
            "Rendered HTML must contain the tab-drafts content div"
        )

    def test_generate_drafts_button_in_html(self):
        html = _render_detail_template()
        assert "Generate AI Drafts" in html, (
            "Rendered HTML must contain the 'Generate AI Drafts' button"
        )

    def test_drafts_table_in_html(self):
        html = _render_detail_template()
        assert 'id="drafts-table"' in html, (
            "Rendered HTML must contain the drafts table element"
        )

    def test_drafts_tbody_in_html(self):
        html = _render_detail_template()
        assert 'id="drafts-tbody"' in html, (
            "Rendered HTML must contain the drafts tbody for dynamic row injection"
        )

    def test_drafts_loading_in_html(self):
        html = _render_detail_template()
        assert 'id="drafts-loading"' in html, (
            "Rendered HTML must contain the drafts loading indicator"
        )

    def test_requirement_column_in_html(self):
        html = _render_detail_template()
        assert "Requirement" in html, "Rendered HTML must contain 'Requirement' column header"

    def test_domain_column_in_html(self):
        html = _render_detail_template()
        assert "Domain" in html, "Rendered HTML must contain 'Domain' column header"

    def test_confidence_column_in_html(self):
        html = _render_detail_template()
        assert "Confidence" in html, "Rendered HTML must contain 'Confidence' column header"

    def test_opp_id_wired_to_draft_action(self):
        html = _render_detail_template()
        assert _OPP_ID in html, (
            "Rendered HTML must embed the opportunity ID for the govconAction call"
        )


# ---------------------------------------------------------------------------
# Route test: GET /proposals/<opp_id> uses proposals/detail.html
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, solicitation_number, title, agency, due_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (_OPP_ID, "FA8650-26-R-0001", "Test RFP", "USAF", "2026-06-30"),
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


class TestProposalsDetailRouteAiDraftsTab:
    """GET /proposals/<opp_id> must render proposals/detail.html with AI Drafts context."""

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

    def test_opp_id_matches(self, flask_app):
        result = self._call_route(flask_app)
        assert result["opp"]["id"] == _OPP_ID

    def test_sections_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "sections" in result, "render_template must receive sections list"

    def test_stats_passed_to_template(self, flask_app):
        result = self._call_route(flask_app)
        assert "stats" in result, "render_template must receive stats dict"

    def test_stats_has_sections_keys(self, flask_app):
        result = self._call_route(flask_app)
        stats = result["stats"]
        assert "sections_total" in stats
        assert "sections_complete" in stats

    def test_compliance_stats_passed(self, flask_app):
        result = self._call_route(flask_app)
        assert "compliance_stats" in result, "render_template must receive compliance_stats"

    def test_question_stats_passed(self, flask_app):
        result = self._call_route(flask_app)
        assert "question_stats" in result, "render_template must receive question_stats"

    def test_amendments_passed(self, flask_app):
        result = self._call_route(flask_app)
        assert "amendments" in result, "render_template must receive amendments list"
