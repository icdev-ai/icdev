# CUI // SP-CTI
"""Tests for /cpmp/<id> EVM tab — S-curve or chart section visible.

The shipped S-curve is a sized, visible ``#evm-scurve-loading`` container plus
an ``#evm-scurve-svg`` element that ``loadEvmScurve(contractId)`` populates from
``/api/cpmp/contracts/<id>/evm/scurve``. These assertions originally named a
single ``#evm-scurve-chart`` div with a ``data-contract-id`` attribute, which is
not what was built; each check below now targets the shipped element while
testing the same property it always tested.

Verifies:
 1. Template source sizes the S-curve container with a min-height.
 2. Template source has display:flex on the container (not hidden).
 3. Template source does not hide the container with display:none.
 4. Template source places chart inside a .card wrapper.
 5. Template source places .card wrapper inside tab-evm panel.
 6. Template source has placeholder text for the pre-fetch state.
 7. Template source has mc-status span for Monte Carlo result injection.
 8. Rendered HTML binds the chart loader to contract.id.
 9. Rendered HTML does not hide chart container with display:none.
10. Rendered HTML contains S-curve card wrapper with card-label.
11. Rendered HTML S-curve section appears after EVM indicator cards.
12. HTTP response contains the container min-height.
13. HTTP response contains display:flex on chart container.
14. HTTP response binds the chart loader to the route contract id.
"""
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shipped S-curve contract (detail.html, EVM tab)
# ---------------------------------------------------------------------------

#: The visible, sized container that holds the placeholder until data arrives.
_SCURVE_CONTAINER = 'id="evm-scurve-loading"'
#: The SVG the renderer draws into. Hidden until loadEvmScurve() has data.
_SCURVE_SVG = 'id="evm-scurve-svg"'
#: Height reserved for the chart so it occupies layout space before rendering.
_SCURVE_MIN_HEIGHT = "min-height:260px"
#: Text shown in the container while the S-curve fetch is in flight.
_SCURVE_PLACEHOLDER = "Loading S-curve"


def _scurve_binding(contract_id: str) -> str:
    """The template binds the chart to a contract through this JS call."""
    return f"loadEvmScurve('{contract_id}')"


def _open_tag(source: str, marker: str) -> str:
    """Return just the element's own opening tag, so a sibling's style cannot
    be mistaken for the container's own."""
    start = source.find(marker)
    if start == -1:
        return ""
    tag_start = source.rfind("<", 0, start)
    return source[tag_start:source.find(">", start) + 1]

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_DETAIL_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "detail.html"

_CONTRACT_ID = "ctr-evm-scurve-0012"
_CONTRACT_NUMBER = "W52P1J-26-C-0012"

_CONTRACT = {
    "id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "title": "EVM S-Curve Visibility Test Contract",
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

_EVM_STANDARD = {
    "cpi": 1.05,
    "spi": 0.97,
    "eac": 519_737,
    "etc": 124_737,
    "vac": -19_737,
    "tcpi": 1.14,
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
    TypeError during render rather than leaving a blank tab.
    """
    return {
        "contract": contract if contract is not None else _CONTRACT,
        "clins": [],
        "wbs_elements": [],
        "deliverables": [],
        "subcontractors": [],
        "evm": evm if evm is not None else _EVM_STANDARD,
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
    # detail.html calls url_for('static', ...) and a bare Environment has no
    # Flask app bound, so without this every render raises UndefinedError.
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
# Tests: template source — chart container visibility properties
# ---------------------------------------------------------------------------


class TestEvmScurveChartContainerSource:
    """detail.html template source must declare the S-curve chart container with
    visible sizing and layout styles."""

    def _source(self):
        return _DETAIL_TEMPLATE.read_text(encoding="utf-8")

    def test_chart_container_has_min_height(self):
        tag = _open_tag(self._source(), _SCURVE_CONTAINER)
        assert _SCURVE_MIN_HEIGHT in tag, (
            f"S-curve container must have {_SCURVE_MIN_HEIGHT} to be visible in the page"
        )

    def test_chart_container_has_display_flex(self):
        tag = _open_tag(self._source(), _SCURVE_CONTAINER)
        assert "display:flex" in tag, (
            "S-curve container must use display:flex so it occupies layout space"
        )

    def test_chart_container_not_display_none(self):
        source = self._source()
        assert _SCURVE_CONTAINER in source, "S-curve container must exist in template"
        # Scoped to the container's own tag: the sibling <svg> is deliberately
        # display:none until loadEvmScurve() has points to draw.
        assert "display:none" not in _open_tag(source, _SCURVE_CONTAINER), (
            "S-curve container must not have display:none — chart must be visible"
        )

    def test_chart_svg_target_present(self):
        assert _SCURVE_SVG in self._source(), (
            "detail.html must declare the <svg> element renderScurveSvg() draws into"
        )

    def test_chart_inside_card_wrapper(self):
        source = self._source()
        card_pos = source.find('class="card"')
        scurve_pos = source.find(_SCURVE_CONTAINER)
        assert card_pos != -1, "S-curve section must be wrapped in a .card div"
        assert scurve_pos != -1, "S-curve container must exist in template"
        assert card_pos < scurve_pos, (
            "S-curve container must be nested inside the .card wrapper"
        )

    def test_card_inside_tab_evm_panel(self):
        source = self._source()
        tab_evm_pos = source.find('id="tab-evm"')
        card_label_pos = source.find("Earned Value S-Curve", tab_evm_pos)
        tab_evm_close_pos = source.find('id="tab-', tab_evm_pos + 1)
        assert tab_evm_pos != -1, "tab-evm panel must exist"
        assert card_label_pos != -1, (
            "Earned Value S-Curve card-label must exist after tab-evm start"
        )
        assert card_label_pos < tab_evm_close_pos, (
            "S-curve card must be inside the tab-evm panel, not outside it"
        )

    def test_chart_placeholder_text_present(self):
        assert _SCURVE_PLACEHOLDER in self._source(), (
            "S-curve container must have placeholder text for initial visible state"
        )

    def test_mc_status_span_present(self):
        assert 'id="mc-status"' in self._source(), (
            "mc-status span must be present to surface Monte Carlo results in the chart section"
        )


# ---------------------------------------------------------------------------
# Tests: rendered HTML — chart section visible in output
# ---------------------------------------------------------------------------


class TestEvmScurveChartRendered:
    """Rendered detail.html must output the S-curve chart container with correct
    contract id binding and no hidden styles."""

    def test_rendered_chart_data_contract_id_matches(self):
        html = _render_detail()
        assert _scurve_binding(_CONTRACT_ID) in html, (
            f"Rendered S-curve must be bound to contract.id '{_CONTRACT_ID}'"
        )

    def test_rendered_chart_not_hidden(self):
        html = _render_detail()
        assert _SCURVE_CONTAINER in html, "S-curve container must be present in rendered HTML"
        assert "display:none" not in _open_tag(html, _SCURVE_CONTAINER), (
            "Rendered S-curve container must not have display:none — it must be visible"
        )

    def test_rendered_contains_scurve_card_label(self):
        html = _render_detail()
        assert "Earned Value S-Curve" in html, (
            "Rendered HTML must contain 'Earned Value S-Curve' card label"
        )

    def test_rendered_scurve_section_after_indicator_cards(self):
        """S-curve chart section must appear after the EVM indicator cards grid."""
        html = _render_detail()
        stat_grid_pos = html.find('class="stat-grid"')
        scurve_pos = html.find(_SCURVE_CONTAINER)
        assert stat_grid_pos != -1, "EVM stat-grid must be present in rendered HTML"
        assert scurve_pos != -1, "S-curve container must be present in rendered HTML"
        assert stat_grid_pos < scurve_pos, (
            "S-curve chart section must appear after EVM indicator cards in rendered output"
        )

    def test_rendered_chart_min_height_preserved(self):
        html = _render_detail()
        assert _SCURVE_MIN_HEIGHT in _open_tag(html, _SCURVE_CONTAINER), (
            f"Rendered S-curve container must retain its {_SCURVE_MIN_HEIGHT} style"
        )

    def test_rendered_chart_display_flex_preserved(self):
        html = _render_detail()
        assert "display:flex" in _open_tag(html, _SCURVE_CONTAINER), (
            "Rendered S-curve container must retain display:flex style"
        )


# ---------------------------------------------------------------------------
# Tests: HTTP response — chart section visible in HTTP response body
# ---------------------------------------------------------------------------


class TestEvmScurveChartHTTPResponse:
    """GET /cpmp/<id> HTTP response body must contain a visible S-curve chart section."""

    @pytest.fixture()
    def http_app(self):
        return _build_http_app()

    def test_response_chart_min_height_present(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert _SCURVE_MIN_HEIGHT in _open_tag(body, _SCURVE_CONTAINER), (
            f"HTTP response must include {_SCURVE_MIN_HEIGHT} on the S-curve container"
        )

    def test_response_chart_display_flex_present(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert "display:flex" in _open_tag(body, _SCURVE_CONTAINER), (
            "HTTP response must include display:flex on the S-curve container"
        )

    def test_response_chart_data_contract_id_matches(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert _scurve_binding(_CONTRACT_ID) in body, (
            f"HTTP response S-curve must be bound to contract id '{_CONTRACT_ID}'"
        )

    def test_response_scurve_card_label_present(self, http_app):
        with http_app.test_client() as c:
            resp = c.get(f"/cpmp/{_CONTRACT_ID}")
        body = resp.get_data(as_text=True)
        assert "Earned Value S-Curve" in body, (
            "HTTP response must include 'Earned Value S-Curve' card label in EVM tab"
        )
