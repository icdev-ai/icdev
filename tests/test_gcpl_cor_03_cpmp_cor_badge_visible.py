# CUI // SP-CTI
"""Tests for /cpmp/cor — Government Read-Only View / COR badge visible.

Verifies:
1. Template source declares the gov-banner div with role="banner".
2. Template source contains "Government Read-Only View" text.
3. Template source contains aria-label="Government read-only notice".
4. Rendered HTML emits the banner with read-only text when contracts list is empty.
5. Rendered HTML emits the banner with read-only text when contracts are present.
6. Page heading "COR Portal" is present in source and rendered HTML.
7. "Contracting Officer Representative" subtitle is present.
8. Footer read-only notice is present in source and rendered HTML.
9. Contract table aria-label is present.
10. Stat grid aria-label is present.
"""
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_COR_PORTAL_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "cor_portal.html"

# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------


def _render_cor_portal(contracts=None, cor_email="cor@agency.mil"):
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
        cor_email=cor_email,
    )


def _make_contract(contract_id="c-cor-01", number="USAF-2026-001", title="Test COR Contract"):
    return {
        "id": contract_id,
        "contract_number": number,
        "title": title,
        "agency": "USAF",
        "status": "active",
        "health": "green",
        "pop_end": "2027-09-30",
        "total_value": 1_500_000.0,
    }


# ---------------------------------------------------------------------------
# Static: template source — gov-banner element
# ---------------------------------------------------------------------------


class TestTemplateSourceGovBanner:
    """cor_portal.html must declare the government read-only banner in source."""

    def test_gov_banner_class_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'class="gov-banner"' in source, (
            "cor_portal.html must have an element with class='gov-banner'"
        )

    def test_gov_banner_role_banner(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'role="banner"' in source, (
            "cor_portal.html gov-banner must have role='banner' for accessibility"
        )

    def test_gov_banner_aria_label(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="Government read-only notice"' in source, (
            "cor_portal.html gov-banner must have aria-label='Government read-only notice'"
        )

    def test_government_readonly_view_text_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "Government Read-Only View" in source, (
            "cor_portal.html must contain the text 'Government Read-Only View' in the banner"
        )

    def test_gov_banner_background_color_defined(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert ".gov-banner" in source, (
            "cor_portal.html must define .gov-banner CSS style"
        )


# ---------------------------------------------------------------------------
# Static: template source — page structure
# ---------------------------------------------------------------------------


class TestTemplateSourcePageStructure:
    """cor_portal.html must declare COR Portal heading and subtitle."""

    def test_cor_portal_heading_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "COR Portal" in source, (
            "cor_portal.html must contain the page heading 'COR Portal'"
        )

    def test_contracting_officer_representative_subtitle(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "Contracting Officer Representative" in source, (
            "cor_portal.html must contain 'Contracting Officer Representative' in the subtitle"
        )

    def test_stat_grid_aria_label_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="COR portal summary statistics"' in source, (
            "cor_portal.html stat grid must have aria-label='COR portal summary statistics'"
        )

    def test_contract_table_aria_label_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="COR contract listing"' in source, (
            "cor_portal.html contract table must have aria-label='COR contract listing'"
        )

    def test_footer_readonly_notice_present(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        assert "read-only access" in source, (
            "cor_portal.html must contain a footer notice about read-only access"
        )

    def test_contract_table_columns_defined(self):
        source = _COR_PORTAL_TEMPLATE.read_text(encoding="utf-8")
        for col in ("Contract #", "Title", "Agency", "Status", "Health", "POP End", "Value"):
            assert col in source, (
                f"cor_portal.html contract table must have column header '{col}'"
            )


# ---------------------------------------------------------------------------
# Rendered HTML: gov-banner visible with no contracts
# ---------------------------------------------------------------------------


class TestRenderedHtmlBannerEmpty:
    """Rendered HTML must show the COR badge even when contracts list is empty."""

    def test_gov_banner_div_rendered(self):
        html = _render_cor_portal(contracts=[])
        assert 'class="gov-banner"' in html, (
            "Rendered HTML must include the gov-banner div"
        )

    def test_government_readonly_view_text_rendered(self):
        html = _render_cor_portal(contracts=[])
        assert "Government Read-Only View" in html, (
            "Rendered HTML must contain 'Government Read-Only View' text"
        )

    def test_role_banner_rendered(self):
        html = _render_cor_portal(contracts=[])
        assert 'role="banner"' in html, (
            "Rendered HTML must include role='banner' on the gov-banner element"
        )

    def test_cor_portal_heading_rendered(self):
        html = _render_cor_portal(contracts=[])
        assert "COR Portal" in html, (
            "Rendered HTML must include the 'COR Portal' heading"
        )

    def test_empty_state_row_rendered(self):
        html = _render_cor_portal(contracts=[])
        assert "No contracts available." in html, (
            "Rendered HTML must show 'No contracts available.' when contracts list is empty"
        )

    def test_footer_readonly_notice_rendered(self):
        html = _render_cor_portal(contracts=[])
        assert "read-only access" in html, (
            "Rendered HTML must include footer read-only access notice"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: gov-banner visible with contracts present
# ---------------------------------------------------------------------------


class TestRenderedHtmlBannerWithContracts:
    """Rendered HTML must show the COR badge when contracts are present."""

    def test_gov_banner_present_with_contracts(self):
        html = _render_cor_portal(contracts=[_make_contract()])
        assert "Government Read-Only View" in html, (
            "Rendered HTML must show 'Government Read-Only View' banner when contracts are present"
        )

    def test_contract_number_rendered(self):
        html = _render_cor_portal(contracts=[_make_contract(number="USAF-2026-001")])
        assert "USAF-2026-001" in html, (
            "Rendered HTML must include the contract number in the table row"
        )

    def test_contract_title_rendered(self):
        html = _render_cor_portal(contracts=[_make_contract(title="Test COR Contract")])
        assert "Test COR Contract" in html, (
            "Rendered HTML must include the contract title in the table row"
        )

    def test_contract_agency_rendered(self):
        html = _render_cor_portal(contracts=[_make_contract()])
        assert "USAF" in html, (
            "Rendered HTML must include the contract agency in the table row"
        )

    def test_active_status_badge_rendered(self):
        html = _render_cor_portal(contracts=[_make_contract()])
        assert "Active" in html, (
            "Rendered HTML must render the 'Active' status badge for an active contract"
        )

    def test_green_health_badge_rendered(self):
        html = _render_cor_portal(contracts=[_make_contract()])
        assert "Green" in html, (
            "Rendered HTML must render the 'Green' health badge for a green contract"
        )

    def test_cor_detail_link_rendered(self):
        html = _render_cor_portal(contracts=[_make_contract(contract_id="c-cor-01")])
        assert "/cpmp/cor/c-cor-01" in html, (
            "Rendered HTML must include a link to /cpmp/cor/<contract_id> for each contract row"
        )

    def test_footer_readonly_notice_with_contracts(self):
        html = _render_cor_portal(contracts=[_make_contract()])
        assert "read-only access" in html, (
            "Rendered HTML must include the footer read-only notice even when contracts are present"
        )

    def test_cor_email_in_footer_rendered(self):
        html = _render_cor_portal(contracts=[], cor_email="jane.doe@mil")
        assert "jane.doe@mil" in html, (
            "Rendered HTML must include the COR email address in the footer contact link"
        )
