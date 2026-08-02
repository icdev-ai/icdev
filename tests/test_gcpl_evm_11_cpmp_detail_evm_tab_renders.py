# CUI // SP-CTI
"""Tests for /cpmp/<id> EVM tab — CPI/SPI/EAC/Earned Value rendered in page.

Verifies:
 1. Template source has tab-evm panel element.
 2. Template source has CPI label in EVM tab.
 3. Template source has SPI label in EVM tab.
 4. Template source has EAC label in EVM tab.
 5. Template source has ETC label in EVM tab.
 6. Template source has VAC label in EVM tab.
 7. Template source has TCPI label in EVM tab.
 8. Template source has evm-scurve-chart element.
 9. Template source has data-contract-id attribute on chart element.
10. Template source has runMonteCarlo JavaScript function.
11. Template source has Monte Carlo Forecast button.
12. Template source has Earned Value S-Curve heading.
13. Rendered HTML with CPI=1.05 displays CPI value.
14. Rendered HTML with SPI=0.97 displays SPI value.
15. Rendered HTML with EAC=519737 displays EAC value.
16. Rendered HTML with ETC=124737 displays ETC value.
17. CPI >= 1.0 renders green color (#28a745).
18. SPI in [0.9, 1.0) renders yellow color (#ffc107).
19. VAC negative renders red color (#dc3545).
20. CPI < 0.9 renders red color (#dc3545).
21. Empty evm dict renders '---' for CPI.
22. Empty evm dict renders '---' for SPI.
23. Empty evm dict renders '---' for EAC.
24. GET /cpmp/<id> returns HTTP 200.
25. HTTP response body contains EVM tab nav button.
26. HTTP response body contains evm-scurve-chart element.
27. evm-scurve-chart data-contract-id matches contract id.
"""
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_DETAIL_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "detail.html"

_CONTRACT_ID = "ctr-evm-tab-0011"
_CONTRACT_NUMBER = "W52P1J-26-C-0011"

_CONTRACT = {
    "id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "title": "EVM Tab Rendering Test Contract",
    "agency": "ARMY",
    "status": "active",
    "health": "green",
    "contract_type": "CPFF",
    "pop_start": "2025-01-01",
    "pop_end": "2027-12-31",
    "cor_name": "Jane COR",
    "cor_email": "cor@agency.mil",
    "naics_code": "541511",
    "total_value": 1_000_000.0,
    "funded_value": 750_000.0,
    "ceiling_value": 1_100_000.0,
}

# Standard EVM scenario: BAC=1M, PV=500K, EV=525K, AC=500K → CPI=1.05, SPI=1.05
_EVM_GREEN = {
    "cpi": 1.05,
    "spi": 1.05,
    "eac": 952_381,
    "etc": 452_381,
    "vac": 47_619,
    "tcpi": 0.9267,
}

# SPI yellow scenario: SPI=0.97 (0.9 <= SPI < 1.0)
_EVM_SPI_YELLOW = {
    "cpi": 1.05,
    "spi": 0.97,
    "eac": 519_737,
    "etc": 124_737,
    "vac": -19_737,
    "tcpi": 1.14,
}

# CPI red scenario: CPI=0.85 (< 0.9)
_EVM_CPI_RED = {
    "cpi": 0.85,
    "spi": 0.90,
    "eac": 1_176_471,
    "etc": 676_471,
    "vac": -176_471,
    "tcpi": 1.40,
}

_STUB_BASE = (
    "{% block title %}{% endblock %}"
    "{% block content %}{% endblock %}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_url_for(endpoint, **values):
    """Stand-in for Flask's url_for when rendering outside an app context."""
    filename = values.get("filename", "")
    return f"/{endpoint}/{filename}".rstrip("/")


def _detail_context(contract=None, evm=None):
    """Full render context for cpmp/detail.html.

    ``milestones``/``milestone_deps`` feed ``{{ … | tojson }}`` in the Schedule
    tab. Jinja's Undefined is not JSON-serializable, so omitting them raises a
    TypeError during render rather than leaving a blank tab — which is how a
    schedule-tab addition broke every EVM-tab test in this file.
    """
    return {
        "contract": contract if contract is not None else _CONTRACT,
        "clins": [],
        "wbs_elements": [],
        "deliverables": [],
        "subcontractors": [],
        "evm": evm if evm is not None else _EVM_SPI_YELLOW,
        "cpars_prediction": {},
        "cpars_assessments": [],
        "milestones": [],
        "milestone_deps": [],
    }


def _render_detail(contract=None, evm=None):
    """Render detail.html via Jinja2 with a stub base template."""
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": _STUB_BASE}),
            FileSystemLoader(str(_TEMPLATES_DIR)),
        ]),
        autoescape=select_autoescape(["html"]),
    )
    # detail.html calls url_for('static', ...); a bare Environment has no Flask
    # app bound, so without this every render raises UndefinedError before a
    # single EVM assertion runs.
    env.globals["url_for"] = _fake_url_for
    tmpl = env.get_template("cpmp/detail.html")
    return tmpl.render(**_detail_context(contract=contract, evm=evm))


def _build_http_app():
    """Build a minimal Flask app serving the CPMP detail page."""
    from flask import Flask, render_template
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    flask_app = Flask(__name__, template_folder=str(_TEMPLATES_DIR))
    flask_app.config["TESTING"] = True
    flask_app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": _STUB_BASE}),
        FileSystemLoader(str(_TEMPLATES_DIR)),
    ])

    @flask_app.route("/cpmp/<contract_id>")
    def cpmp_detail(contract_id):
        return render_template("cpmp/detail.html", **_detail_context())

    return flask_app


# ---------------------------------------------------------------------------
# Tests: template source structure
# ---------------------------------------------------------------------------


class TestEvmTabTemplateSource:
    """detail.html template source must declare the EVM tab with all required elements."""

    def _source(self):
        return _DETAIL_TEMPLATE.read_text(encoding="utf-8")

    def test_tab_evm_panel_present(self):
        assert 'id="tab-evm"' in self._source(), (
            "detail.html must have <div id='tab-evm'> EVM tab panel"
        )

    def test_cpi_label_present(self):
        source = self._source()
        assert ">CPI<" in source, (
            "detail.html EVM tab must have a CPI label element"
        )

    def test_spi_label_present(self):
        source = self._source()
        assert ">SPI<" in source, (
            "detail.html EVM tab must have an SPI label element"
        )

    def test_eac_label_present(self):
        source = self._source()
        assert ">EAC<" in source, (
            "detail.html EVM tab must have an EAC label element"
        )

    def test_etc_label_present(self):
        source = self._source()
        assert ">ETC<" in source, (
            "detail.html EVM tab must have an ETC label element"
        )

    def test_vac_label_present(self):
        source = self._source()
        assert ">VAC<" in source, (
            "detail.html EVM tab must have a VAC label element"
        )

    def test_tcpi_label_present(self):
        source = self._source()
        assert ">TCPI<" in source, (
            "detail.html EVM tab must have a TCPI label element"
        )

    def test_scurve_chart_element_present(self):
        source = self._source()
        # The shipped S-curve is a sized #evm-scurve-loading placeholder plus the
        # #evm-scurve-svg element renderScurveSvg() draws into — not the single
        # #evm-scurve-chart div this test was originally written against.
        assert 'id="evm-scurve-loading"' in source, (
            "detail.html must have <div id='evm-scurve-loading'> for the S-curve placeholder"
        )
        assert 'id="evm-scurve-svg"' in source, (
            "detail.html must have <svg id='evm-scurve-svg'> for S-curve rendering"
        )

    def test_data_contract_id_attribute_present(self):
        source = self._source()
        # The chart is bound to its contract through the loader call, not a
        # data- attribute.
        assert "loadEvmScurve('{{ contract.id }}')" in source, (
            "detail.html must bind the S-curve loader to contract.id"
        )

    def test_run_monte_carlo_function_present(self):
        source = self._source()
        assert "runMonteCarlo" in source, (
            "detail.html must define the runMonteCarlo JavaScript function"
        )

    def test_monte_carlo_button_present(self):
        source = self._source()
        assert "Monte Carlo" in source, (
            "detail.html EVM tab must have a Monte Carlo Forecast button"
        )

    def test_earned_value_scurve_heading_present(self):
        source = self._source()
        assert "Earned Value S-Curve" in source, (
            "detail.html EVM tab must have 'Earned Value S-Curve' heading"
        )


# ---------------------------------------------------------------------------
# Tests: rendered HTML — CPI/SPI/EAC/EV values
# ---------------------------------------------------------------------------


class TestEvmTabRenderedValues:
    """Rendered detail.html must display CPI/SPI/EAC/ETC values from the evm context."""

    def test_cpi_value_rendered(self):
        html = _render_detail(evm=_EVM_SPI_YELLOW)
        assert "1.05" in html, (
            "Rendered EVM tab must display CPI value 1.05"
        )

    def test_spi_value_rendered(self):
        html = _render_detail(evm=_EVM_SPI_YELLOW)
        assert "0.97" in html, (
            "Rendered EVM tab must display SPI value 0.97"
        )

    def test_eac_value_rendered(self):
        html = _render_detail(evm=_EVM_SPI_YELLOW)
        assert "519737" in html, (
            "Rendered EVM tab must display EAC value as integer dollars"
        )

    def test_etc_value_rendered(self):
        html = _render_detail(evm=_EVM_SPI_YELLOW)
        assert "124737" in html, (
            "Rendered EVM tab must display ETC value as integer dollars"
        )


# ---------------------------------------------------------------------------
# Tests: rendered HTML — status color thresholds
# ---------------------------------------------------------------------------


class TestEvmTabRenderedColors:
    """EVM status thresholds must drive rendered color styles."""

    def test_cpi_ge_1_renders_green(self):
        """CPI = 1.05 >= 1.0 — must render green (#28a745)."""
        html = _render_detail(evm=_EVM_GREEN)
        assert "#28a745" in html, (
            "CPI >= 1.0 must render green color #28a745"
        )

    def test_spi_between_09_and_10_renders_yellow(self):
        """SPI = 0.97 is in [0.9, 1.0) — must render yellow (#ffc107)."""
        html = _render_detail(evm=_EVM_SPI_YELLOW)
        assert "#ffc107" in html, (
            "SPI in [0.9, 1.0) must render yellow color #ffc107"
        )

    def test_negative_vac_renders_red(self):
        """VAC = -19737 < 0 — must render red (#dc3545)."""
        html = _render_detail(evm=_EVM_SPI_YELLOW)
        assert "#dc3545" in html, (
            "Negative VAC must render red color #dc3545"
        )

    def test_cpi_below_09_renders_red(self):
        """CPI = 0.85 < 0.9 — must render red (#dc3545)."""
        html = _render_detail(evm=_EVM_CPI_RED)
        assert "#dc3545" in html, (
            "CPI < 0.9 must render red color #dc3545"
        )


# ---------------------------------------------------------------------------
# Tests: rendered HTML — empty evm dict shows placeholders
# ---------------------------------------------------------------------------


class TestEvmTabRenderedNoData:
    """When evm context is empty, EVM tab must render '---' placeholders."""

    def test_empty_evm_cpi_shows_placeholder(self):
        html = _render_detail(evm={})
        assert "---" in html, (
            "Empty evm dict must render '---' placeholder for CPI"
        )

    def test_empty_evm_spi_shows_placeholder(self):
        html = _render_detail(evm={})
        assert ">SPI<" in html, (
            "EVM tab must still show SPI label even when evm data is absent"
        )

    def test_empty_evm_eac_shows_placeholder(self):
        html = _render_detail(evm={})
        assert ">EAC<" in html, (
            "EVM tab must still show EAC label even when evm data is absent"
        )


# ---------------------------------------------------------------------------
# Tests: HTTP response
# ---------------------------------------------------------------------------


class TestEvmTabHTTPResponse:
    """GET /cpmp/<id> must return 200 with EVM tab content in HTML body."""

    @pytest.fixture()
    def http_app(self):
        return _build_http_app()

    def test_returns_200(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        assert resp.status_code == 200, (
            f"GET /cpmp/<id> must return HTTP 200, got {resp.status_code}"
        )

    def test_response_is_html(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        assert "text/html" in resp.content_type, (
            "GET /cpmp/<id> must return text/html content-type"
        )

    def test_response_contains_evm_tab_button(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert "EVM" in body, (
            "HTTP response body must contain EVM tab nav button"
        )

    def test_response_contains_scurve_chart_element(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert "evm-scurve-svg" in body, (
            "HTTP response body must contain the evm-scurve-svg chart element"
        )

    def test_scurve_chart_data_contract_id_matches(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert f"loadEvmScurve('{_CONTRACT_ID}')" in body, (
            f"S-curve loader must be bound to contract id '{_CONTRACT_ID}'"
        )
