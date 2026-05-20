# CUI // SP-CTI
"""Tests for GET /cpmp/cor/<contract_id> — HTTP < 400, CUI banner present, no edit controls.

Verifies:
 1. GET /cpmp/cor/<id> returns HTTP 200.
 2. HTTP response status is strictly less than 400.
 3. Response content-type is text/html.
 4. Response body contains the gov-banner element.
 5. Template source has gov-banner class element.
 6. Template source has role="banner" on the banner element.
 7. Template source has aria-label="Government read-only notice".
 8. Template source contains "Government Read-Only View" text.
 9. Template source defines .gov-banner CSS style.
10. Template source contains footer read-only notice.
11. Rendered HTML contains gov-banner class.
12. Rendered HTML contains "Government Read-Only View" text.
13. Rendered HTML contains role="banner".
14. Rendered HTML contains aria-label="Government read-only notice".
15. Rendered HTML contains footer read-only notice.
16. Template source contains no <form> element.
17. Template source contains no <input> element.
18. Template source contains no edit button text.
19. Template source contains no delete button text.
20. Edit/delete control count in template source is ≤ 2.
21. Rendered HTML contains no <form> tag.
22. Rendered HTML contains no <input> tag.
23. Edit button count in rendered HTML is ≤ 2.
24. Delete button count in rendered HTML is ≤ 2.
25. Combined edit/delete control count in rendered HTML is ≤ 2.
"""
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_COR_DETAIL_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "cor_detail.html"

_CONTRACT_ID = "ctr-cor-detail-0011"
_COR_EMAIL = "cor.detail@agency.mil"

_CONTRACT = {
    "id": _CONTRACT_ID,
    "contract_number": "DOD-2026-0011",
    "title": "Test COR Detail Contract",
    "agency": "DOD",
    "status": "active",
    "health": "green",
    "contract_type": "cost_plus_fixed_fee",
    "pop_start": "2025-01-01",
    "pop_end": "2027-12-31",
    "cor_name": "Jane COR",
    "cor_email": _COR_EMAIL,
    "total_value": 5_000_000.0,
}

_EVM = {
    "cpi": 1.02,
    "spi": 0.98,
    "bac": 5_000_000,
    "pv": 2_500_000,
    "ev": 2_450_000,
    "schedule_variance": -50_000,
    "eac": 4_900_000,
    "percent_complete_schedule": 0.49,
}

_CPARS_ASSESSMENTS = [
    {
        "id": "cpars-detail-01",
        "contract_id": _CONTRACT_ID,
        "period_start": "2025-01",
        "period_end": "2025-06",
        "quality_rating": "very_good",
        "schedule_rating": "satisfactory",
        "cost_rating": "satisfactory",
        "management_rating": "very_good",
        "small_business_rating": None,
        "overall_rating": "very_good",
        "assessment_status": "final",
    }
]

_STUB_BASE = (
    "{% block title %}{% endblock %}"
    "{% block content %}{% endblock %}"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_cor_detail(
    contract=None,
    deliverables=None,
    evm=None,
    cpars_assessments=None,
    cor_email=_COR_EMAIL,
):
    """Render cor_detail.html via Jinja2 with a stub base template."""
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": _STUB_BASE}),
            FileSystemLoader(str(_TEMPLATES_DIR)),
        ]),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("cpmp/cor_detail.html")
    return tmpl.render(
        contract=contract if contract is not None else _CONTRACT,
        deliverables=deliverables if deliverables is not None else [],
        evm=evm if evm is not None else _EVM,
        cpars_assessments=cpars_assessments if cpars_assessments is not None else _CPARS_ASSESSMENTS,
        cor_email=cor_email,
    )


def _build_http_app():
    """Build a minimal Flask test app that serves the COR detail page."""
    from flask import Flask, render_template
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    flask_app = Flask(__name__, template_folder=str(_TEMPLATES_DIR))
    flask_app.config["TESTING"] = True
    flask_app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": _STUB_BASE}),
        FileSystemLoader(str(_TEMPLATES_DIR)),
    ])

    @flask_app.route("/cpmp/cor/<contract_id>")
    def cor_detail(contract_id):
        return render_template(
            "cpmp/cor_detail.html",
            contract=_CONTRACT,
            deliverables=[],
            evm=_EVM,
            cpars_assessments=_CPARS_ASSESSMENTS,
            cor_email=_COR_EMAIL,
        )

    return flask_app


def _count_edit_controls(html: str) -> int:
    """Count buttons and links in html that carry edit or delete intent."""
    edit_buttons = re.findall(r"(?i)<button[^>]*>[^<]*edit[^<]*</button>", html)
    delete_buttons = re.findall(r"(?i)<button[^>]*>[^<]*delete[^<]*</button>", html)
    edit_links = re.findall(
        r'(?i)<a[^>]+href=["\'][^"\']*(?:edit|delete)[^"\']*["\']', html
    )
    edit_link_text = re.findall(r"(?i)<a[^>]*>[^<]*(?:edit|delete)[^<]*</a>", html)
    return len(edit_buttons) + len(delete_buttons) + len(edit_links) + len(edit_link_text)


# ---------------------------------------------------------------------------
# Tests: HTTP response
# ---------------------------------------------------------------------------


class TestCORDetailHTTPResponse:
    """GET /cpmp/cor/<id> must return HTTP 200 with an HTML body."""

    @pytest.fixture()
    def http_app(self):
        return _build_http_app()

    def test_returns_200(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/cor/{_CONTRACT_ID}")
        assert resp.status_code == 200

    def test_status_is_below_400(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/cor/{_CONTRACT_ID}")
        assert resp.status_code < 400, (
            f"COR detail page must return HTTP < 400, got {resp.status_code}"
        )

    def test_response_is_html(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/cor/{_CONTRACT_ID}")
        assert "text/html" in resp.content_type, (
            "COR detail page must return text/html content-type"
        )

    def test_response_body_contains_gov_banner(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/cor/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert "gov-banner" in body, (
            "HTTP response body must contain the gov-banner element"
        )


# ---------------------------------------------------------------------------
# Tests: CUI banner — template source
# ---------------------------------------------------------------------------


class TestCORDetailCUIBannerSource:
    """cor_detail.html template source must declare the CUI/government read-only banner."""

    def test_gov_banner_class_present(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'class="gov-banner"' in source, (
            "cor_detail.html must have an element with class='gov-banner'"
        )

    def test_gov_banner_role_banner(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'role="banner"' in source, (
            "cor_detail.html gov-banner must have role='banner' for accessibility"
        )

    def test_gov_banner_aria_label(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="Government read-only notice"' in source, (
            "cor_detail.html gov-banner must have aria-label='Government read-only notice'"
        )

    def test_government_readonly_view_text_present(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Government Read-Only View" in source, (
            "cor_detail.html must contain 'Government Read-Only View' in the banner"
        )

    def test_gov_banner_css_defined(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert ".gov-banner" in source, (
            "cor_detail.html must define .gov-banner CSS style"
        )

    def test_footer_readonly_notice_present(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "read-only" in source.lower(), (
            "cor_detail.html must contain a footer read-only notice"
        )


# ---------------------------------------------------------------------------
# Tests: CUI banner — rendered HTML
# ---------------------------------------------------------------------------


class TestCORDetailCUIBannerRendered:
    """Rendered HTML must emit the CUI banner with proper accessibility attributes."""

    def test_banner_class_in_rendered_html(self):
        html = _render_cor_detail()
        assert 'class="gov-banner"' in html, (
            "Rendered HTML must include the gov-banner div"
        )

    def test_readonly_text_in_rendered_html(self):
        html = _render_cor_detail()
        assert "Government Read-Only View" in html, (
            "Rendered HTML must contain 'Government Read-Only View' text"
        )

    def test_role_banner_in_rendered_html(self):
        html = _render_cor_detail()
        assert 'role="banner"' in html, (
            "Rendered HTML must include role='banner' on the gov-banner element"
        )

    def test_aria_label_in_rendered_html(self):
        html = _render_cor_detail()
        assert 'aria-label="Government read-only notice"' in html, (
            "Rendered HTML must include aria-label='Government read-only notice'"
        )

    def test_footer_notice_in_rendered_html(self):
        html = _render_cor_detail()
        assert "read-only" in html.lower(), (
            "Rendered HTML must include the footer read-only notice"
        )


# ---------------------------------------------------------------------------
# Tests: no edit controls — template source
# ---------------------------------------------------------------------------


class TestCORDetailReadOnlyControlsSource:
    """cor_detail.html template source must not contain edit/delete controls (≤2 allowed)."""

    def test_no_form_element(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<form" not in source.lower(), (
            "cor_detail.html must not contain any <form> elements"
        )

    def test_no_input_element(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "<input" not in source.lower(), (
            "cor_detail.html must not contain any <input> elements"
        )

    def test_no_edit_button_text(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        edit_btns = re.findall(r"(?i)<button[^>]*>[^<]*edit[^<]*</button>", source)
        assert len(edit_btns) == 0, (
            f"cor_detail.html must not contain buttons with 'edit' text, found {len(edit_btns)}"
        )

    def test_no_delete_button_text(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        delete_btns = re.findall(r"(?i)<button[^>]*>[^<]*delete[^<]*</button>", source)
        assert len(delete_btns) == 0, (
            f"cor_detail.html must not contain buttons with 'delete' text, found {len(delete_btns)}"
        )

    def test_edit_delete_count_not_exceeded(self):
        source = _COR_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        count = _count_edit_controls(source)
        assert count <= 2, (
            f"cor_detail.html must have ≤2 edit/delete controls, found {count}"
        )


# ---------------------------------------------------------------------------
# Tests: no edit controls — rendered HTML
# ---------------------------------------------------------------------------


class TestCORDetailReadOnlyControlsRendered:
    """Rendered HTML must not contain edit/delete controls (≤2 allowed)."""

    def test_no_form_tag_in_rendered_html(self):
        html = _render_cor_detail()
        assert "<form" not in html.lower(), (
            "Rendered HTML must not contain any <form> elements"
        )

    def test_no_input_tag_in_rendered_html(self):
        html = _render_cor_detail()
        assert "<input" not in html.lower(), (
            "Rendered HTML must not contain any <input> elements"
        )

    def test_edit_button_count_le_two(self):
        html = _render_cor_detail()
        edit_btns = re.findall(r"(?i)<button[^>]*>[^<]*edit[^<]*</button>", html)
        assert len(edit_btns) <= 2, (
            f"Rendered HTML must have ≤2 edit buttons, found {len(edit_btns)}"
        )

    def test_delete_button_count_le_two(self):
        html = _render_cor_detail()
        delete_btns = re.findall(r"(?i)<button[^>]*>[^<]*delete[^<]*</button>", html)
        assert len(delete_btns) <= 2, (
            f"Rendered HTML must have ≤2 delete buttons, found {len(delete_btns)}"
        )

    def test_combined_edit_delete_count_le_two(self):
        html = _render_cor_detail()
        count = _count_edit_controls(html)
        assert count <= 2, (
            f"Rendered HTML must have ≤2 combined edit/delete controls, found {count}"
        )
