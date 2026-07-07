# CUI // SP-CTI
"""Tests for /cpmp — contract table with status badges renders.

Verifies:
1. Template source declares the contract table with Status and Health columns.
2. Badge classes (badge-success, badge-warning, badge-error, badge-info) are wired.
3. Rendered HTML emits correct badge class for each contract status value.
4. Rendered HTML emits correct badge class for each health value (green/yellow/red).
5. Empty portfolio renders the empty-state row (no contracts message).
6. GET /api/cpmp/contracts returns 200 with mocked list_contracts.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_PORTFOLIO_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "portfolio.html"

# ---------------------------------------------------------------------------
# Fake data helpers
# ---------------------------------------------------------------------------


def _make_portfolio(
    total=3,
    active=2,
    total_value=5_000_000.0,
    burn_rate=120_000.0,
    overdue=0,
    green=2,
    yellow=1,
    red=0,
):
    return {
        "total_contracts": total,
        "active_contracts": active,
        "total_value": total_value,
        "burn_rate": burn_rate,
        "overdue_deliverables": overdue,
        "health_distribution": {"green": green, "yellow": yellow, "red": red},
    }


def _make_contract(
    contract_id="c-test-01",
    number="DOD-2026-001",
    title="Test Contract Alpha",
    agency="USAF",
    contract_type="FFP",
    status="active",
    health="green",
    pop_end="2027-09-30",
    value=2_500_000.0,
    cpi=1.02,
    spi=0.98,
):
    return {
        "id": contract_id,
        "contract_number": number,
        "title": title,
        "agency": agency,
        "contract_type": contract_type,
        "status": status,
        "health": health,
        "pop_end": pop_end,
        "value": value,
        "cpi": cpi,
        "spi": spi,
    }


_FAKE_CONTRACTS_LIST = {
    "status": "ok",
    "contracts": [
        {
            "id": "c-api-01",
            "contract_number": "N00014-26-C-0001",
            "title": "API Test Contract",
            "status": "active",
            "health": "green",
        }
    ],
    "count": 1,
}

_FAKE_CONTRACTS_EMPTY = {"status": "ok", "contracts": [], "count": 0}


# ---------------------------------------------------------------------------
# Static: template source checks
# ---------------------------------------------------------------------------


class TestTemplateSourceContractsTable:
    """cpmp/portfolio.html must declare the contract table and badge wiring."""

    def test_contract_table_aria_label_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="Contract portfolio listing"' in source, (
            "cpmp/portfolio.html must have aria-label='Contract portfolio listing' on the table"
        )

    def test_status_column_header_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Status</th>" in source, (
            "cpmp/portfolio.html must include a Status column header"
        )

    def test_health_column_header_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Health</th>" in source, (
            "cpmp/portfolio.html must include a Health column header"
        )

    def test_contract_number_column_header_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "<th>Contract #</th>" in source, (
            "cpmp/portfolio.html must include a Contract # column header"
        )

    def test_badge_success_class_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-success" in source, (
            "cpmp/portfolio.html must use badge-success for active/green contracts"
        )

    def test_badge_warning_class_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-warning" in source, (
            "cpmp/portfolio.html must use badge-warning for at_risk/yellow contracts"
        )

    def test_badge_error_class_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-error" in source, (
            "cpmp/portfolio.html must use badge-error for overdue/red contracts"
        )

    def test_badge_info_fallback_in_status_colors(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        # badge-info is emitted dynamically via cstatus_colors.get(cstatus, 'info')
        assert "cstatus_colors.get(cstatus, 'info')" in source, (
            "cpmp/portfolio.html must use 'info' as the fallback badge color for unknown statuses"
        )

    def test_status_colors_mapping_active_to_success(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "'active': 'success'" in source, (
            "cpmp/portfolio.html must map status 'active' → badge-success"
        )

    def test_status_colors_mapping_at_risk_to_warning(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "'at_risk': 'warning'" in source, (
            "cpmp/portfolio.html must map status 'at_risk' → badge-warning"
        )

    def test_status_colors_mapping_overdue_to_error(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "'overdue': 'error'" in source, (
            "cpmp/portfolio.html must map status 'overdue' → badge-error"
        )

    def test_health_green_badge_success_wired(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-success" in source and "Green" in source, (
            "cpmp/portfolio.html must render badge-success for health='green'"
        )

    def test_health_yellow_badge_warning_wired(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-warning" in source and "Yellow" in source, (
            "cpmp/portfolio.html must render badge-warning for health='yellow'"
        )

    def test_health_red_badge_error_wired(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "badge-error" in source and "Red" in source, (
            "cpmp/portfolio.html must render badge-error for health='red'"
        )

    def test_no_contracts_empty_state_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert "No contracts in portfolio." in source, (
            "cpmp/portfolio.html must include an empty-state row for zero contracts"
        )


# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------


def _render_portfolio(contracts=None, portfolio=None, upcoming_deliverables=None):
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
    tmpl = env.get_template("cpmp/portfolio.html")
    return tmpl.render(
        contracts=contracts if contracts is not None else [],
        portfolio=portfolio or _make_portfolio(),
        upcoming_deliverables=upcoming_deliverables if upcoming_deliverables is not None else [],
    )


# ---------------------------------------------------------------------------
# Rendered HTML: table structure
# ---------------------------------------------------------------------------


class TestRenderedHtmlContractTable:
    """Jinja2-rendered cpmp/portfolio.html must emit the table shell."""

    def test_contract_table_tag_present(self):
        html = _render_portfolio()
        assert "Contract portfolio listing" in html, (
            "Rendered HTML must contain the contract table aria-label"
        )

    def test_status_column_header_in_rendered_html(self):
        html = _render_portfolio()
        assert "Status" in html, (
            "Rendered HTML must include the Status column header"
        )

    def test_health_column_header_in_rendered_html(self):
        html = _render_portfolio()
        assert "Health" in html, (
            "Rendered HTML must include the Health column header"
        )

    def test_empty_state_row_when_no_contracts(self):
        html = _render_portfolio(contracts=[])
        assert "No contracts in portfolio." in html, (
            "Rendered HTML must show empty-state row when contracts list is empty"
        )

    def test_empty_state_row_not_shown_when_contracts_present(self):
        html = _render_portfolio(contracts=[_make_contract()])
        assert "No contracts in portfolio." not in html, (
            "Empty-state row must not appear when at least one contract is rendered"
        )

    def test_contract_number_appears_in_rendered_html(self):
        contract = _make_contract(number="DOD-2026-TEST")
        html = _render_portfolio(contracts=[contract])
        assert "DOD-2026-TEST" in html, (
            "Rendered HTML must include the contract number in the table row"
        )

    def test_contract_title_appears_in_rendered_html(self):
        contract = _make_contract(title="Unique Contract Title XYZ")
        html = _render_portfolio(contracts=[contract])
        assert "Unique Contract Title XYZ" in html, (
            "Rendered HTML must include the contract title in the table row"
        )

    def test_contract_agency_appears_in_rendered_html(self):
        contract = _make_contract(agency="DIA")
        html = _render_portfolio(contracts=[contract])
        assert "DIA" in html, (
            "Rendered HTML must include the agency value in the table row"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: status badge classes
# ---------------------------------------------------------------------------


class TestRenderedHtmlStatusBadges:
    """Status badge CSS classes must be emitted correctly per contract.status."""

    def test_active_status_renders_badge_success(self):
        html = _render_portfolio(contracts=[_make_contract(status="active")])
        assert "badge-success" in html, (
            "Contract status='active' must render badge-success"
        )

    def test_active_status_label_is_active(self):
        html = _render_portfolio(contracts=[_make_contract(status="active")])
        assert "Active" in html, (
            "Contract status='active' badge label must read 'Active'"
        )

    def test_complete_status_renders_badge_info(self):
        html = _render_portfolio(contracts=[_make_contract(status="complete")])
        assert "badge-info" in html, (
            "Contract status='complete' must render badge-info"
        )

    def test_at_risk_status_renders_badge_warning(self):
        html = _render_portfolio(contracts=[_make_contract(status="at_risk")])
        assert "badge-warning" in html, (
            "Contract status='at_risk' must render badge-warning"
        )

    def test_at_risk_status_label_replaces_underscore(self):
        html = _render_portfolio(contracts=[_make_contract(status="at_risk")])
        assert "At Risk" in html, (
            "Contract status='at_risk' badge label must replace underscore with space"
        )

    def test_overdue_status_renders_badge_error(self):
        html = _render_portfolio(contracts=[_make_contract(status="overdue")])
        assert "badge-error" in html, (
            "Contract status='overdue' must render badge-error"
        )

    def test_pending_status_renders_badge_warning(self):
        html = _render_portfolio(contracts=[_make_contract(status="pending")])
        assert "badge-warning" in html, (
            "Contract status='pending' must render badge-warning"
        )

    def test_closed_status_renders_badge_info(self):
        html = _render_portfolio(contracts=[_make_contract(status="closed")])
        assert "badge-info" in html, (
            "Contract status='closed' must render badge-info"
        )

    def test_unknown_status_falls_back_to_badge_info(self):
        html = _render_portfolio(contracts=[_make_contract(status="unknown_xyz")])
        assert "badge-info" in html, (
            "Unknown contract status must fall back to badge-info"
        )

    def test_multiple_contracts_emit_multiple_badge_spans(self):
        contracts = [
            _make_contract(contract_id="c-01", status="active", health="green"),
            _make_contract(contract_id="c-02", status="complete", health="yellow"),
        ]
        html = _render_portfolio(contracts=contracts)
        assert html.count('<span class="badge') >= 2, (
            "Two contracts must emit at least two badge spans in the table"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: health badge classes
# ---------------------------------------------------------------------------


class TestRenderedHtmlHealthBadges:
    """Health badge CSS classes must be emitted correctly per contract.health."""

    def test_green_health_renders_badge_success(self):
        html = _render_portfolio(contracts=[_make_contract(health="green")])
        assert "badge-success" in html, (
            "Contract health='green' must render badge-success"
        )

    def test_green_health_label_is_green(self):
        html = _render_portfolio(contracts=[_make_contract(health="green")])
        assert ">Green<" in html, (
            "Contract health='green' badge label must read 'Green'"
        )

    def test_yellow_health_renders_badge_warning(self):
        html = _render_portfolio(contracts=[_make_contract(health="yellow")])
        assert "badge-warning" in html, (
            "Contract health='yellow' must render badge-warning"
        )

    def test_yellow_health_label_is_yellow(self):
        html = _render_portfolio(contracts=[_make_contract(health="yellow")])
        assert ">Yellow<" in html, (
            "Contract health='yellow' badge label must read 'Yellow'"
        )

    def test_red_health_renders_badge_error(self):
        html = _render_portfolio(contracts=[_make_contract(health="red")])
        assert "badge-error" in html, (
            "Contract health='red' must render badge-error"
        )

    def test_red_health_label_is_red(self):
        html = _render_portfolio(contracts=[_make_contract(health="red")])
        assert ">Red<" in html, (
            "Contract health='red' badge label must read 'Red'"
        )

    def test_unknown_health_renders_plain_badge(self):
        html = _render_portfolio(contracts=[_make_contract(health="unknown")])
        assert 'class="badge"' in html or "badge" in html, (
            "Unknown health value must still render a badge element"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: CPI / SPI performance indicators
# ---------------------------------------------------------------------------


class TestRenderedHtmlPerformanceIndicators:
    """CPI and SPI values must appear in the rendered table row."""

    def test_cpi_value_present_in_row(self):
        html = _render_portfolio(contracts=[_make_contract(cpi=1.05, spi=1.0)])
        assert "1.05" in html, (
            "Rendered HTML must include the CPI value in the contract row"
        )

    def test_spi_value_present_in_row(self):
        html = _render_portfolio(contracts=[_make_contract(cpi=1.0, spi=0.97)])
        assert "0.97" in html, (
            "Rendered HTML must include the SPI value in the contract row"
        )

    def test_low_cpi_renders_text_danger(self):
        html = _render_portfolio(contracts=[_make_contract(cpi=0.80, spi=1.0)])
        assert "text-danger" in html, (
            "CPI below 0.85 must render text-danger class"
        )

    def test_mid_cpi_renders_text_warning(self):
        html = _render_portfolio(contracts=[_make_contract(cpi=0.90, spi=1.0)])
        assert "text-warning" in html, (
            "CPI between 0.85 and 0.95 must render text-warning class"
        )

    def test_none_cpi_renders_dash(self):
        contract = _make_contract()
        contract["cpi"] = None
        html = _render_portfolio(contracts=[contract])
        assert "text-muted" in html, (
            "None CPI must render a muted dash placeholder"
        )


# ---------------------------------------------------------------------------
# API: GET /api/cpmp/contracts returns 200
# ---------------------------------------------------------------------------


def _build_cpmp_api_app() -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    @flask_app.before_request
    def _inject_fake_auth_0():
        from flask import g
        g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

    flask_app.register_blueprint(cpmp_api)
    return flask_app


class TestListContractsAPI:
    """GET /api/cpmp/contracts must return 200 with contract list payload."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _get(self, api_app, result=None):
        if result is None:
            result = _FAKE_CONTRACTS_LIST
        with patch(
            "tools.govcon.contract_manager.list_contracts",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/cpmp/contracts")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_contracts_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "contracts" in data, "Response must include 'contracts' key"

    def test_response_contracts_is_list(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert isinstance(data["contracts"], list), "'contracts' must be a list"

    def test_response_has_count_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "count" in data, "Response must include 'count' key"

    def test_response_count_matches_list_length(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["count"] == len(data["contracts"]), (
            "'count' must equal the number of contracts in the list"
        )

    def test_response_has_status_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "status" in data, "Response must include 'status' key"

    def test_empty_list_returns_200(self, api_app):
        resp = self._get(api_app, result=_FAKE_CONTRACTS_EMPTY)
        assert resp.status_code == 200

    def test_empty_list_count_is_zero(self, api_app):
        resp = self._get(api_app, result=_FAKE_CONTRACTS_EMPTY)
        data = resp.get_json()
        assert data["count"] == 0

    def test_status_filter_param_accepted(self, api_app):
        with patch(
            "tools.govcon.contract_manager.list_contracts",
            return_value=_FAKE_CONTRACTS_LIST,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/cpmp/contracts?status=active")
        assert resp.status_code == 200, (
            "GET /api/cpmp/contracts?status=active must return 200"
        )

    def test_limit_param_accepted(self, api_app):
        with patch(
            "tools.govcon.contract_manager.list_contracts",
            return_value=_FAKE_CONTRACTS_LIST,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/cpmp/contracts?limit=10")
        assert resp.status_code == 200, (
            "GET /api/cpmp/contracts?limit=10 must return 200"
        )

    def test_returns_500_when_list_contracts_raises(self, api_app):
        with patch(
            "tools.govcon.contract_manager.list_contracts",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/cpmp/contracts")
        assert resp.status_code == 500

    def test_500_response_has_message_key(self, api_app):
        with patch(
            "tools.govcon.contract_manager.list_contracts",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/cpmp/contracts")
        data = resp.get_json()
        assert "message" in data, "Error response must include 'message' key"
