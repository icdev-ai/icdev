# CUI // SP-CTI
"""Tests for /cpmp/<id> — every contract detail tab renders.

Verifies:
1. Template source declares all tab buttons (Overview, Periods, CLINs, WBS,
   Deliverables, EVM, Schedule, Subcontractors, CPARS, Modifications, Risks)
   with correct switchTab() calls.
2. Template source declares all tab panel divs with correct IDs.
3. Rendered HTML includes all tab panel IDs when given a minimal contract context.
4. Overview tab panel is active by default (class="tab-panel active").
5. Each non-active tab panel is present but not initially active.
"""
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_DETAIL_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "detail.html"

# ---------------------------------------------------------------------------
# Expected tab definitions
# ---------------------------------------------------------------------------

_EXPECTED_TABS = [
    ("overview", "Overview"),
    ("periods", "Periods"),
    ("clins", "CLINs"),
    ("wbs", "WBS"),
    ("deliverables", "Deliverables"),
    ("evm", "EVM"),
    ("schedule", "Schedule"),
    ("subcontractors", "Subcontractors"),
    ("cpars", "CPARS"),
    ("mods", "Modifications"),
    ("risks", "Risks"),
]

#: Tab count the detail page ships. The original spec listed ten; the Periods
#: tab (obligation periods, gcpl-ctr-02) landed afterwards and is a real
#: eleventh tab, so the exact-count assertions below track eleven.
_TAB_COUNT = len(_EXPECTED_TABS)


def _has_tab_label(source: str, label: str) -> bool:
    """True when *source* declares a tab button whose visible text is *label*.

    Each button now carries a trailing ``<span class="tab-help-btn">?</span>``,
    so the label is followed by a space rather than by ``<``. Matching on
    ``>Overview<`` therefore fails even though the button is present — five of
    these assertions were failing for exactly that reason while the other five
    passed only because the same word appears elsewhere in the template.
    """
    return f">{label}<" in source or f">{label} <" in source


# ---------------------------------------------------------------------------
# Fake data helpers
# ---------------------------------------------------------------------------


def _make_contract(contract_id="c-test-23", number="DOD-2026-023", title="Test Detail Contract"):
    return {
        "id": contract_id,
        "contract_number": number,
        "title": title,
        "agency": "USAF",
        "contract_type": "FFP",
        "status": "active",
        "health": "green",
        "pop_start": "2026-01-01",
        "pop_end": "2027-09-30",
        "cor_name": "Jane Doe",
        "cor_email": "jane.doe@mil",
        "naics_code": "541330",
        "total_value": 2_500_000.0,
        "funded_value": 2_000_000.0,
        "ceiling_value": 3_000_000.0,
    }


def _make_evm():
    return {
        "cpi": 1.02,
        "spi": 0.98,
        "eac": 2_450_000.0,
        "etc": 450_000.0,
        "vac": 50_000.0,
        "tcpi": 1.01,
    }


# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------


def _render_detail(
    contract=None,
    clins=None,
    wbs_elements=None,
    deliverables=None,
    subcontractors=None,
    evm=None,
    cpars_prediction=None,
    cpars_assessments=None,
    milestones=None,
    milestone_deps=None,
):
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
    # detail.html calls url_for('static', ...); a bare Environment has no Flask
    # app bound, so without this stub every render raises UndefinedError before
    # a single tab assertion runs.
    env.globals["url_for"] = lambda endpoint, **values: (
        f"/{endpoint}/{values.get('filename', '')}".rstrip("/")
    )
    tmpl = env.get_template("cpmp/detail.html")
    return tmpl.render(
        contract=contract if contract is not None else _make_contract(),
        clins=clins if clins is not None else [],
        wbs_elements=wbs_elements if wbs_elements is not None else [],
        deliverables=deliverables if deliverables is not None else [],
        subcontractors=subcontractors if subcontractors is not None else [],
        evm=evm if evm is not None else _make_evm(),
        cpars_prediction=cpars_prediction,
        cpars_assessments=cpars_assessments if cpars_assessments is not None else [],
        milestones=milestones if milestones is not None else [],
        milestone_deps=milestone_deps if milestone_deps is not None else [],
    )


# ---------------------------------------------------------------------------
# Static: template source declares all 10 tab buttons
# ---------------------------------------------------------------------------


class TestTemplateSourceTabButtons:
    """cpmp/detail.html must declare all 10 tab navigation buttons."""

    def test_overview_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('overview')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('overview')"
        )

    def test_clins_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('clins')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('clins')"
        )

    def test_wbs_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('wbs')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('wbs')"
        )

    def test_deliverables_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('deliverables')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('deliverables')"
        )

    def test_evm_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('evm')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('evm')"
        )

    def test_subcontractors_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('subcontractors')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('subcontractors')"
        )

    def test_cpars_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('cpars')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('cpars')"
        )

    def test_schedule_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('schedule')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('schedule')"
        )

    def test_mods_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('mods')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('mods')"
        )

    def test_risks_tab_button_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "switchTab('risks')" in source, (
            "cpmp/detail.html must have a tab button calling switchTab('risks')"
        )

    def test_overview_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "Overview"), (
            "cpmp/detail.html must have a tab button labeled 'Overview'"
        )

    def test_clins_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "CLINs"), (
            "cpmp/detail.html must have a tab button labeled 'CLINs'"
        )

    def test_wbs_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "WBS"), (
            "cpmp/detail.html must have a tab button labeled 'WBS'"
        )

    def test_deliverables_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "Deliverables"), (
            "cpmp/detail.html must have a tab button labeled 'Deliverables'"
        )

    def test_evm_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "EVM"), (
            "cpmp/detail.html must have a tab button labeled 'EVM'"
        )

    def test_subcontractors_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "Subcontractors"), (
            "cpmp/detail.html must have a tab button labeled 'Subcontractors'"
        )

    def test_cpars_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "CPARS"), (
            "cpmp/detail.html must have a tab button labeled 'CPARS'"
        )

    def test_schedule_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "Schedule"), (
            "cpmp/detail.html must have a tab button labeled 'Schedule'"
        )

    def test_mods_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "Modifications"), (
            "cpmp/detail.html must have a tab button labeled 'Modifications'"
        )

    def test_risks_button_label_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert _has_tab_label(source, "Risks"), (
            "cpmp/detail.html must have a tab button labeled 'Risks'"
        )


# ---------------------------------------------------------------------------
# Static: template source declares all 10 tab panel divs
# ---------------------------------------------------------------------------


class TestTemplateSourceTabPanels:
    """cpmp/detail.html must declare all 10 tab panel divs with correct IDs."""

    def test_overview_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-overview"' in source, (
            "cpmp/detail.html must have id='tab-overview' on the Overview panel div"
        )

    def test_clins_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-clins"' in source, (
            "cpmp/detail.html must have id='tab-clins' on the CLINs panel div"
        )

    def test_wbs_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-wbs"' in source, (
            "cpmp/detail.html must have id='tab-wbs' on the WBS panel div"
        )

    def test_deliverables_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-deliverables"' in source, (
            "cpmp/detail.html must have id='tab-deliverables' on the Deliverables panel div"
        )

    def test_evm_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-evm"' in source, (
            "cpmp/detail.html must have id='tab-evm' on the EVM panel div"
        )

    def test_subcontractors_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-subcontractors"' in source, (
            "cpmp/detail.html must have id='tab-subcontractors' on the Subcontractors panel div"
        )

    def test_cpars_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-cpars"' in source, (
            "cpmp/detail.html must have id='tab-cpars' on the CPARS panel div"
        )

    def test_schedule_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-schedule"' in source, (
            "cpmp/detail.html must have id='tab-schedule' on the Schedule panel div"
        )

    def test_mods_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-mods"' in source, (
            "cpmp/detail.html must have id='tab-mods' on the Modifications panel div"
        )

    def test_risks_panel_id_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-risks"' in source, (
            "cpmp/detail.html must have id='tab-risks' on the Risks panel div"
        )

    def test_ten_tab_panels_total(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        count = source.count('class="tab-panel')
        assert count == _TAB_COUNT, (
            f"cpmp/detail.html must declare exactly {_TAB_COUNT} tab-panel divs, found {count}"
        )

    def test_overview_panel_is_active_by_default(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="tab-overview" class="tab-panel active"' in source, (
            "cpmp/detail.html must render the Overview panel as active by default"
        )

    def test_switchTab_function_defined(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "function switchTab(" in source, (
            "cpmp/detail.html must define the switchTab() JavaScript function"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: all 7 panels present
# ---------------------------------------------------------------------------


class TestRenderedHtmlTabPanels:
    """Jinja2-rendered cpmp/detail.html must emit all 10 tab panel IDs."""

    def test_overview_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-overview"' in html, (
            "Rendered HTML must include id='tab-overview'"
        )

    def test_clins_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-clins"' in html, (
            "Rendered HTML must include id='tab-clins'"
        )

    def test_wbs_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-wbs"' in html, (
            "Rendered HTML must include id='tab-wbs'"
        )

    def test_deliverables_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-deliverables"' in html, (
            "Rendered HTML must include id='tab-deliverables'"
        )

    def test_evm_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-evm"' in html, (
            "Rendered HTML must include id='tab-evm'"
        )

    def test_subcontractors_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-subcontractors"' in html, (
            "Rendered HTML must include id='tab-subcontractors'"
        )

    def test_cpars_panel_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-cpars"' in html, (
            "Rendered HTML must include id='tab-cpars'"
        )

    def test_overview_is_active_in_rendered_html(self):
        html = _render_detail()
        assert 'id="tab-overview" class="tab-panel active"' in html, (
            "Rendered HTML must mark Overview as the active tab panel"
        )

    def test_tab_nav_present_in_rendered_html(self):
        html = _render_detail()
        assert 'class="tab-nav"' in html, (
            "Rendered HTML must include the tab-nav container"
        )

    def test_ten_tab_panel_divs_in_rendered_html(self):
        html = _render_detail()
        count = html.count('class="tab-panel')
        assert count == _TAB_COUNT, (
            f"Rendered HTML must contain exactly {_TAB_COUNT} tab-panel divs, found {count}"
        )

    def test_all_tab_button_labels_in_rendered_html(self):
        html = _render_detail()
        for _tab_id, label in _EXPECTED_TABS:
            assert label in html, (
                f"Rendered HTML must include the '{label}' tab button label"
            )


# ---------------------------------------------------------------------------
# Rendered HTML: empty-state rows per tab
# ---------------------------------------------------------------------------


class TestRenderedHtmlEmptyStates:
    """Each data tab must render an empty-state row when given no data."""

    def test_clins_empty_state_row(self):
        html = _render_detail(clins=[])
        assert "No CLINs defined." in html, (
            "CLINs tab must show 'No CLINs defined.' when clins list is empty"
        )

    def test_wbs_empty_state_row(self):
        html = _render_detail(wbs_elements=[])
        assert "No WBS elements defined." in html, (
            "WBS tab must show 'No WBS elements defined.' when wbs_elements list is empty"
        )

    def test_deliverables_empty_state_row(self):
        html = _render_detail(deliverables=[])
        assert "No deliverables defined." in html, (
            "Deliverables tab must show 'No deliverables defined.' when deliverables list is empty"
        )

    def test_subcontractors_empty_state_row(self):
        html = _render_detail(subcontractors=[])
        assert "No subcontractors defined." in html, (
            "Subcontractors tab must show 'No subcontractors defined.' when subcontractors list is empty"
        )

    def test_cpars_empty_state_row(self):
        html = _render_detail(cpars_assessments=[])
        assert "No CPARS assessments recorded." in html, (
            "CPARS tab must show 'No CPARS assessments recorded.' when cpars_assessments list is empty"
        )
