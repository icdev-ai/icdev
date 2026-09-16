# CUI // SP-CTI
"""Smoke tests for /cpmp portfolio dashboard — HTTP < 400, no server error.

Verifies:
1. /cpmp returns HTTP 200 (< 400) with valid portfolio data.
2. render_template receives expected context keys (portfolio, contracts,
   upcoming_deliverables).
3. Route degrades gracefully when get_portfolio_summary raises — still
   returns 200, not a 5xx server error.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# Fake data
# ---------------------------------------------------------------------------

_FAKE_PORTFOLIO_RESULT = {
    "portfolio": {
        "total_contracts": 3,
        "active_contracts": 2,
        "total_value": 4_500_000.00,
        "burn_rate_pct": 62.5,
        "overdue_deliverables": 1,
        "at_risk_contracts": 0,
        "health_distribution": {"green": 2, "yellow": 1, "red": 0},
        "contracts": [
            {
                "id": "c-001",
                "name": "Sentinel Cloud Modernization",
                "status": "active",
                "health": "green",
            },
            {
                "id": "c-002",
                "name": "DataLake Integration",
                "status": "active",
                "health": "yellow",
            },
        ],
        "upcoming_deliverables": [
            {"id": "d-001", "title": "CDR Package", "due_date": "2026-06-01"},
        ],
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_cpmp_test_app() -> Flask:
    """Return a minimal Flask app with only the CPMP page route registered."""
    flask_app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
        ),
    )
    flask_app.config["TESTING"] = True

    # /cpmp is wrapped in @require_role(...), which abort(401)s unless
    # g.current_user is set. This bare app registers no auth hook, so without
    # this every request 401s and the route under test never runs. Mirrors
    # tests/test_gcpl_perf_11_cpmp_negative_event_create.py.
    @flask_app.before_request
    def _inject_test_user():
        from flask import g

        g.current_user = {
            "id": "test-admin",
            "username": "test_user",
            "role": "admin",
            "email": "test@test.mil",
            "classification": "CUI",
        }

    def _get_db():  # not used by /cpmp, but _register_govcon_pages requires it
        return None

    from tools.dashboard.app import _register_govcon_pages

    _register_govcon_pages(flask_app, _get_db)
    return flask_app


def _call_cpmp(flask_app: Flask, portfolio_return=_FAKE_PORTFOLIO_RESULT) -> dict:
    """GET /cpmp, intercept render_template; return captured kwargs + _status."""
    captured = {}

    def fake_render(template_name, **kwargs):
        captured.update(kwargs)
        captured["_template"] = template_name
        return "OK"

    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=portfolio_return,
    ):
        with patch("tools.dashboard.app.render_template", side_effect=fake_render):
            with flask_app.test_client() as c:
                resp = c.get("/cpmp")
                captured["_status"] = resp.status_code

    return captured


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def cpmp_app():
    return _build_cpmp_test_app()


# ---------------------------------------------------------------------------
# HTTP status tests
# ---------------------------------------------------------------------------

class TestCpmpPortfolioHTTPStatus:
    """GET /cpmp must return HTTP < 400 (no server error)."""

    def test_returns_200(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert result["_status"] == 200

    def test_status_below_400(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert result["_status"] < 400, f"Expected HTTP < 400, got {result['_status']}"

    def test_not_500(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert result["_status"] != 500

    def test_not_503(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert result["_status"] != 503


# ---------------------------------------------------------------------------
# Template context tests
# ---------------------------------------------------------------------------

class TestCpmpPortfolioTemplateContext:
    """render_template receives the expected context variables."""

    def test_template_name_is_cpmp_portfolio(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert result["_template"] == "cpmp/portfolio.html"

    def test_context_has_portfolio_key(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert "portfolio" in result

    def test_context_has_contracts_key(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert "contracts" in result

    def test_context_has_upcoming_deliverables_key(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert "upcoming_deliverables" in result

    def test_portfolio_is_dict(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert isinstance(result["portfolio"], dict)

    def test_contracts_is_list(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert isinstance(result["contracts"], list)

    def test_upcoming_deliverables_is_list(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert isinstance(result["upcoming_deliverables"], list)

    def test_portfolio_has_total_contracts(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert "total_contracts" in result["portfolio"]

    def test_portfolio_has_health_distribution(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert "health_distribution" in result["portfolio"]

    def test_portfolio_health_distribution_has_green_yellow_red(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        hd = result["portfolio"]["health_distribution"]
        for key in ("green", "yellow", "red"):
            assert key in hd, f"health_distribution missing '{key}'"

    def test_contracts_count_matches_portfolio(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert len(result["contracts"]) == 2


# ---------------------------------------------------------------------------
# Graceful degradation — exception in get_portfolio_summary
# ---------------------------------------------------------------------------

class TestCpmpPortfolioDegradation:
    """Route must not raise 5xx when get_portfolio_summary fails."""

    def _call_with_exception(self, flask_app: Flask) -> dict:
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_template"] = template_name
            return "OK"

        with patch(
            "tools.govcon.portfolio_manager.get_portfolio_summary",
            side_effect=RuntimeError("DB offline"),
        ):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with flask_app.test_client() as c:
                    resp = c.get("/cpmp")
                    captured["_status"] = resp.status_code

        return captured

    def test_exception_still_returns_200(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert result["_status"] == 200

    def test_exception_status_below_400(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert result["_status"] < 400

    def test_exception_renders_cpmp_portfolio_template(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert result["_template"] == "cpmp/portfolio.html"

    def test_exception_portfolio_total_contracts_defaults_to_zero(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert result["portfolio"]["total_contracts"] == 0

    def test_exception_contracts_defaults_to_empty_list(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert result["contracts"] == []

    def test_exception_upcoming_deliverables_defaults_to_empty_list(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert result["upcoming_deliverables"] == []

    def test_exception_passes_error_key(self, cpmp_app):
        result = self._call_with_exception(cpmp_app)
        assert "error" in result


# ---------------------------------------------------------------------------
# Empty portfolio — route handles zero-data gracefully
# ---------------------------------------------------------------------------

class TestCpmpPortfolioEmptyData:
    """Route handles an all-zero portfolio response without errors."""

    _EMPTY_RESULT = {
        "portfolio": {
            "total_contracts": 0,
            "active_contracts": 0,
            "total_value": 0,
            "burn_rate_pct": 0,
            "overdue_deliverables": 0,
            "at_risk_contracts": 0,
            "health_distribution": {"green": 0, "yellow": 0, "red": 0},
            "contracts": [],
            "upcoming_deliverables": [],
        }
    }

    def test_empty_portfolio_returns_200(self, cpmp_app):
        result = _call_cpmp(cpmp_app, portfolio_return=self._EMPTY_RESULT)
        assert result["_status"] == 200

    def test_empty_portfolio_contracts_is_empty_list(self, cpmp_app):
        result = _call_cpmp(cpmp_app, portfolio_return=self._EMPTY_RESULT)
        assert result["contracts"] == []

    def test_empty_portfolio_total_contracts_is_zero(self, cpmp_app):
        result = _call_cpmp(cpmp_app, portfolio_return=self._EMPTY_RESULT)
        assert result["portfolio"]["total_contracts"] == 0


# ---------------------------------------------------------------------------
# Regression: /cpmp must not recompute a per-contract health matrix the
# template never reads (gcpl-cset-06 — qa-fail-0b822ea439ccffe6).
#
# compute_contract_health() does 5 SELECTs plus an UPDATE + commit per
# contract; the route used to call it once per row in `contracts` to build a
# `health_matrix` value that cpmp/portfolio.html never references (it builds
# its own health matrix client-side from `contracts`, which already carries
# cpi/spi). Because e2e fixtures accumulate contracts across runs with no
# cleanup, that made /cpmp do unbounded, wholly wasted DB writes on every
# load — enough to blow past a 30s Playwright navigation timeout on a
# contended host. Fixed by dropping the dead computation.
# ---------------------------------------------------------------------------

class TestCpmpPortfolioSkipsUnusedHealthMatrix:
    """/cpmp must not call compute_contract_health per row for an unused value."""

    def test_compute_contract_health_never_called(self, cpmp_app):
        with patch("tools.govcon.portfolio_manager.compute_contract_health") as mock_health:
            _call_cpmp(cpmp_app)
        mock_health.assert_not_called()

    def test_context_has_no_health_matrix_key(self, cpmp_app):
        result = _call_cpmp(cpmp_app)
        assert "health_matrix" not in result

    def test_exception_path_context_has_no_health_matrix_key(self, cpmp_app):
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_template"] = template_name
            return "OK"

        with patch(
            "tools.govcon.portfolio_manager.get_portfolio_summary",
            side_effect=RuntimeError("DB offline"),
        ):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with cpmp_app.test_client() as c:
                    c.get("/cpmp")

        assert "health_matrix" not in captured
