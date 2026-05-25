# CUI // SP-CTI
"""Tests for /cpmp/cor — 'Government Read-Only View' or COR badge visible.

Verifies:
1. Template source declares the Government Read-Only View banner.
2. Banner has role="banner" and aria-label="Government read-only notice".
3. Template declares read-only access footer notice.
4. Rendered HTML emits the Government Read-Only View text.
5. COR contract listing table aria-label present in source and rendered HTML.
6. Status and health badge wiring matches portfolio.html conventions.
7. Empty-state row emitted when no contracts are assigned.
8. _sanitize_for_cor strips all COR_HIDDEN_FIELDS from a dict and list.
9. GET /api/cpmp/cor/contracts returns 200 with contracts list for a COR user.
10. Response contains no hidden fields (subcontractor_pricing, billed_value, etc.).
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_COR_PORTAL_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "cor_portal.html"

# ---------------------------------------------------------------------------
# Fake data helpers
# ---------------------------------------------------------------------------


def _make_contract(
    contract_id="cor-test-01",
    number="DOD-2026-COR-001",
    title="COR Test Contract Alpha",
    agency="USAF",
    status="active",
    health="green",
    pop_end="2027-09-30",
    total_value=2_500_000.0,
):
    return {
        "id": contract_id,
        "contract_number": number,
        "title": title,
        "agency": agency,
        "status": status,
        "health": health,
        "pop_end": pop_end,
        "total_value": total_value,
    }


def _make_contract_with_hidden(contract_id="cor-hidden-01"):
    return {
        "id": contract_id,
        "contract_number": "DOD-2026-HIDDEN",
        "title": "Contract With Hidden Fields",
        "agency": "DIA",
        "status": "active",
        "health": "green",
        "total_value": 1_000_000.0,
        # COR_HIDDEN_FIELDS — must all be stripped
        "subcontractor_pricing": 500_000.0,
        "internal_cost_details": "sensitive cost breakdown",
        "internal_notes": "not for COR",
        "corrective_action_details": "internal CAP data",
        "billed_value": 750_000.0,
        "ac_cumulative": 800_000.0,
    }


_FAKE_COR_CONTRACTS = [
    {
        "id": "cor-api-01",
        "contract_number": "N00014-26-C-0001",
        "title": "API COR Test Contract",
        "agency": "NAVY",
        "status": "active",
        "health": "green",
        "total_value": 3_000_000.0,
    }
]

_COR_USER = {"id": "usr-cor-01", "email": "cor@agency.gov", "role": "cor"}


# ---------------------------------------------------------------------------
# Static: template source — Government Read-Only View banner
# ---------------------------------------------------------------------------


class TestCORPortalTemplateBannerSource:
    """cor_portal.html must declare the Government Read-Only View banner in source."""

    def test_gov_banner_div_class_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'class="gov-banner"' in source, (
            "cor_portal.html must have a div with class 'gov-banner'"
        )

    def test_gov_banner_role_banner(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'role="banner"' in source, (
            "gov-banner must declare role='banner' for accessibility"
        )

    def test_gov_banner_aria_label_government_notice(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="Government read-only notice"' in source, (
            "gov-banner must have aria-label='Government read-only notice'"
        )

    def test_gov_banner_text_government_read_only_view(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "Government Read-Only View" in source, (
            "cor_portal.html must contain 'Government Read-Only View' text in the banner"
        )

    def test_gov_banner_shield_icon(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "[Shield]" in source or "shield" in source.lower(), (
            "cor_portal.html must include a shield icon or text in the government banner"
        )

    def test_footer_read_only_access_notice(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "read-only access" in source, (
            "cor_portal.html must include a 'read-only access' notice in the footer"
        )

    def test_cor_contract_listing_aria_label(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="COR contract listing"' in source, (
            "cor_portal.html must have a table with aria-label='COR contract listing'"
        )

    def test_stat_grid_aria_label(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="COR portal summary statistics"' in source, (
            "cor_portal.html must include a stat grid with aria-label='COR portal summary statistics'"
        )

    def test_contract_number_column_header_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Contract #</th>" in source, (
            "cor_portal.html must include a 'Contract #' column header"
        )

    def test_status_column_header_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Status</th>" in source, (
            "cor_portal.html must include a 'Status' column header"
        )

    def test_health_column_header_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Health</th>" in source, (
            "cor_portal.html must include a 'Health' column header"
        )

    def test_badge_success_class_wired(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-success" in source, (
            "cor_portal.html must include badge-success for active/green contracts"
        )

    def test_badge_warning_class_wired(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-warning" in source, (
            "cor_portal.html must include badge-warning for at_risk/yellow contracts"
        )

    def test_badge_error_class_wired(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-error" in source, (
            "cor_portal.html must include badge-error for overdue/red contracts"
        )

    def test_no_contracts_empty_state_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "No contracts available." in source, (
            "cor_portal.html must include an empty-state row for zero contracts"
        )

    def test_cor_portal_page_title_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "COR Portal" in source, (
            "cor_portal.html must declare 'COR Portal' in the page title or heading"
        )


# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------


def _render_cor_portal(contracts=None, cor_email=None):
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
    tmpl = env.get_template("cpmp/cor_portal.html")
    return tmpl.render(
        contracts=contracts if contracts is not None else [],
        cor_email=cor_email or "",
    )


# ---------------------------------------------------------------------------
# Rendered HTML: Government Read-Only View banner
# ---------------------------------------------------------------------------


class TestCORPortalRenderedBanner:
    """Rendered cor_portal.html must emit the Government Read-Only View banner."""

    def test_government_read_only_view_text_in_html(self):
        html = _render_cor_portal()
        assert "Government Read-Only View" in html, (
            "Rendered cor_portal.html must contain 'Government Read-Only View'"
        )

    def test_read_only_text_present_in_rendered_html(self):
        html = _render_cor_portal()
        assert "Read-Only" in html, (
            "Rendered cor_portal.html must contain 'Read-Only' in the government banner"
        )

    def test_cor_portal_heading_rendered(self):
        html = _render_cor_portal()
        assert "COR Portal" in html, (
            "Rendered cor_portal.html must include the 'COR Portal' heading"
        )

    def test_cor_contract_listing_table_rendered(self):
        html = _render_cor_portal()
        assert "COR contract listing" in html, (
            "Rendered HTML must include the COR contract listing table"
        )

    def test_empty_state_row_when_no_contracts(self):
        html = _render_cor_portal(contracts=[])
        assert "No contracts available." in html, (
            "Rendered HTML must show empty-state row when no contracts are assigned"
        )

    def test_empty_state_not_shown_when_contracts_present(self):
        html = _render_cor_portal(contracts=[_make_contract()])
        assert "No contracts available." not in html, (
            "Empty-state row must not appear when at least one contract is rendered"
        )

    def test_footer_read_only_notice_rendered(self):
        html = _render_cor_portal()
        assert "read-only access" in html, (
            "Rendered HTML must include the 'read-only access' footer notice"
        )

    def test_contract_number_rendered_in_row(self):
        html = _render_cor_portal(contracts=[_make_contract(number="COR-2026-TESTNUM")])
        assert "COR-2026-TESTNUM" in html, (
            "Rendered HTML must include the contract number in the table row"
        )

    def test_contract_title_rendered_in_row(self):
        html = _render_cor_portal(contracts=[_make_contract(title="Unique COR Title QQQ")])
        assert "Unique COR Title QQQ" in html, (
            "Rendered HTML must include the contract title in the table row"
        )

    def test_contract_agency_rendered_in_row(self):
        html = _render_cor_portal(contracts=[_make_contract(agency="DISA")])
        assert "DISA" in html, (
            "Rendered HTML must include the agency in the table row"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: Status badge classes
# ---------------------------------------------------------------------------


class TestCORPortalRenderedStatusBadges:
    """Status badge CSS classes must be emitted correctly for the COR portal."""

    def test_active_status_renders_badge_success(self):
        html = _render_cor_portal(contracts=[_make_contract(status="active")])
        assert "badge-success" in html, (
            "Contract status='active' must render badge-success in the COR portal"
        )

    def test_active_status_label_is_active(self):
        html = _render_cor_portal(contracts=[_make_contract(status="active")])
        assert "Active" in html, (
            "Contract status='active' badge label must read 'Active'"
        )

    def test_at_risk_status_renders_badge_warning(self):
        html = _render_cor_portal(contracts=[_make_contract(status="at_risk")])
        assert "badge-warning" in html, (
            "Contract status='at_risk' must render badge-warning in the COR portal"
        )

    def test_overdue_status_renders_badge_error(self):
        html = _render_cor_portal(contracts=[_make_contract(status="overdue")])
        assert "badge-error" in html, (
            "Contract status='overdue' must render badge-error in the COR portal"
        )

    def test_unknown_status_falls_back_to_badge_info(self):
        html = _render_cor_portal(contracts=[_make_contract(status="unknown_xyz")])
        assert "badge-info" in html, (
            "Unknown status must fall back to badge-info in the COR portal"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: Health badge classes
# ---------------------------------------------------------------------------


class TestCORPortalRenderedHealthBadges:
    """Health badge CSS classes must be emitted correctly for the COR portal."""

    def test_green_health_renders_badge_success(self):
        html = _render_cor_portal(contracts=[_make_contract(health="green")])
        assert "badge-success" in html, (
            "Health='green' must render badge-success in the COR portal"
        )

    def test_green_health_label_is_green(self):
        html = _render_cor_portal(contracts=[_make_contract(health="green")])
        assert ">Green<" in html, (
            "Health='green' badge label must read 'Green'"
        )

    def test_yellow_health_renders_badge_warning(self):
        html = _render_cor_portal(contracts=[_make_contract(health="yellow")])
        assert "badge-warning" in html, (
            "Health='yellow' must render badge-warning in the COR portal"
        )

    def test_yellow_health_label_is_yellow(self):
        html = _render_cor_portal(contracts=[_make_contract(health="yellow")])
        assert ">Yellow<" in html, (
            "Health='yellow' badge label must read 'Yellow'"
        )

    def test_red_health_renders_badge_error(self):
        html = _render_cor_portal(contracts=[_make_contract(health="red")])
        assert "badge-error" in html, (
            "Health='red' must render badge-error in the COR portal"
        )

    def test_red_health_label_is_red(self):
        html = _render_cor_portal(contracts=[_make_contract(health="red")])
        assert ">Red<" in html, (
            "Health='red' badge label must read 'Red'"
        )


# ---------------------------------------------------------------------------
# Unit: _sanitize_for_cor removes all COR_HIDDEN_FIELDS
# ---------------------------------------------------------------------------


class TestSanitizeForCOR:
    """_sanitize_for_cor must remove every field listed in COR_HIDDEN_FIELDS."""

    @pytest.fixture()
    def sanitize(self):
        from tools.dashboard.api.cpmp import _sanitize_for_cor
        return _sanitize_for_cor

    def test_removes_subcontractor_pricing(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        assert "subcontractor_pricing" not in result

    def test_removes_internal_cost_details(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        assert "internal_cost_details" not in result

    def test_removes_internal_notes(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        assert "internal_notes" not in result

    def test_removes_corrective_action_details(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        assert "corrective_action_details" not in result

    def test_removes_billed_value(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        assert "billed_value" not in result

    def test_removes_ac_cumulative(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        assert "ac_cumulative" not in result

    def test_retains_allowed_public_fields(self, sanitize):
        result = sanitize(_make_contract_with_hidden())
        for field in ("id", "contract_number", "title", "agency", "status", "health", "total_value"):
            assert field in result, f"Allowed field '{field}' must be retained after COR sanitization"

    def test_sanitizes_list_of_dicts(self, sanitize):
        data = [_make_contract_with_hidden("c-01"), _make_contract_with_hidden("c-02")]
        results = sanitize(data)
        assert isinstance(results, list) and len(results) == 2
        for item in results:
            assert "billed_value" not in item

    def test_passthrough_non_dict_scalar_values(self, sanitize):
        assert sanitize("plain string") == "plain string"
        assert sanitize(42) == 42
        assert sanitize(None) is None


# ---------------------------------------------------------------------------
# API: GET /api/cpmp/cor/contracts
# ---------------------------------------------------------------------------


def _build_cor_api_app(user=None) -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(cpmp_api)

    _user = user or _COR_USER

    @flask_app.before_request
    def _inject_user():
        g.current_user = _user

    return flask_app


class TestCORListContractsAPI:
    """GET /api/cpmp/cor/contracts returns 200 with contracts list for a COR user."""

    @pytest.fixture()
    def api_app(self):
        return _build_cor_api_app()

    def _call(self, api_app, contracts=None):
        _contracts = contracts if contracts is not None else _FAKE_COR_CONTRACTS
        mock_conn = MagicMock()
        with patch("tools.dashboard.api.cpmp._get_db", return_value=mock_conn), \
             patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=_contracts):
            with api_app.test_client() as c:
                resp = c.get("/api/cpmp/cor/contracts")
        return resp

    def test_returns_200(self, api_app):
        assert self._call(api_app).status_code == 200

    def test_response_is_json(self, api_app):
        assert self._call(api_app).content_type.startswith("application/json")

    def test_response_status_is_ok(self, api_app):
        data = self._call(api_app).get_json()
        assert data["status"] == "ok"

    def test_response_has_contracts_key(self, api_app):
        data = self._call(api_app).get_json()
        assert "contracts" in data, "Response must include 'contracts' key"

    def test_contracts_value_is_list(self, api_app):
        data = self._call(api_app).get_json()
        assert isinstance(data["contracts"], list), "'contracts' must be a list"

    def test_response_has_total_key(self, api_app):
        data = self._call(api_app).get_json()
        assert "total" in data, "Response must include 'total' key"

    def test_total_equals_contracts_list_length(self, api_app):
        data = self._call(api_app).get_json()
        assert data["total"] == len(data["contracts"]), (
            "'total' must equal the number of contracts returned"
        )

    def test_empty_contracts_returns_200(self, api_app):
        assert self._call(api_app, contracts=[]).status_code == 200

    def test_empty_contracts_total_is_zero(self, api_app):
        data = self._call(api_app, contracts=[]).get_json()
        assert data["total"] == 0

    def test_hidden_fields_stripped_from_response(self, api_app):
        resp = self._call(api_app, contracts=[_make_contract_with_hidden()])
        data = resp.get_json()
        for contract in data["contracts"]:
            for hidden in (
                "subcontractor_pricing",
                "internal_cost_details",
                "internal_notes",
                "corrective_action_details",
                "billed_value",
                "ac_cumulative",
            ):
                assert hidden not in contract, (
                    f"COR API response must not expose hidden field '{hidden}'"
                )

    def test_missing_email_returns_400(self):
        app = _build_cor_api_app(user={"id": "usr-no-email", "role": "cor"})
        with app.test_client() as c:
            resp = c.get("/api/cpmp/cor/contracts")
        assert resp.status_code == 400

    def test_missing_email_response_has_message_key(self):
        app = _build_cor_api_app(user={"id": "usr-no-email", "role": "cor"})
        with app.test_client() as c:
            resp = c.get("/api/cpmp/cor/contracts")
        data = resp.get_json()
        assert "message" in data, "400 response must include 'message' key"

    def test_unauthenticated_returns_401(self):
        from tools.dashboard.api.cpmp import cpmp_api

        flask_app = Flask(__name__)
        flask_app.config["TESTING"] = True
        flask_app.register_blueprint(cpmp_api)

        @flask_app.before_request
        def _no_user():
            g.current_user = None

        with flask_app.test_client() as c:
            resp = c.get("/api/cpmp/cor/contracts")
        assert resp.status_code == 401
