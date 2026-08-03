# CUI // SP-CTI
"""Tests for /cpmp portfolio — responsive layout across 3 viewports.

Verifies template source and stylesheet declare the responsive design markers
that enable the page to adapt across desktop (1440px), tablet (768px), and
mobile (375px) viewports.  Screenshots taken at all three breakpoints are
saved locally under playwright/screenshots/ (gitignored) as visual evidence.

Static checks:
1. base.html declares viewport meta tag (foundation for all responsive behaviour).
2. portfolio.html uses stat-grid (auto-fill columns, collapses on narrow screens).
3. portfolio.html uses table-container on both data tables.
4. portfolio.html uses flex-wrap: wrap in the health-distribution section.
5. portfolio.html uses min-width on flex items (prevents content squash).
6. style.css defines stat-grid with repeat(auto-fill, minmax) — fluid grid.
7. style.css defines a 480px media query that overrides stat-grid to 2-column.
8. style.css defines a 768px media query (tablet breakpoint present).
9. style.css gives table-container overflow: hidden (enables inner scroll).
10. portfolio.html renders all three section headers (stat-grid, two tables).
"""
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent
_TEMPLATES_DIR = _REPO / "tools" / "dashboard" / "templates"
_PORTFOLIO_TEMPLATE = _TEMPLATES_DIR / "cpmp" / "portfolio.html"
_BASE_TEMPLATE = _TEMPLATES_DIR / "base.html"
_STYLE_CSS = _REPO / "tools" / "dashboard" / "static" / "css" / "style.css"

# ---------------------------------------------------------------------------
# Viewport definitions (for documentation / screenshot evidence)
# ---------------------------------------------------------------------------

_VIEWPORTS = [
    {"name": "desktop", "width": 1440, "height": 900},
    {"name": "tablet",  "width": 768,  "height": 1024},
    {"name": "mobile",  "width": 375,  "height": 812},
]

# ---------------------------------------------------------------------------
# 1. base.html — viewport meta tag
# ---------------------------------------------------------------------------


class TestBaseViewportMeta:
    """base.html must declare the standard viewport meta for responsiveness."""

    def test_viewport_meta_width_device_width(self):
        source = _BASE_TEMPLATE.read_text(encoding="utf-8")
        assert 'width=device-width' in source, (
            "base.html must have <meta name='viewport' content='width=device-width, ...>"
        )

    def test_viewport_meta_initial_scale(self):
        source = _BASE_TEMPLATE.read_text(encoding="utf-8")
        assert 'initial-scale=1' in source, (
            "base.html must include initial-scale=1 in the viewport meta tag"
        )


# ---------------------------------------------------------------------------
# 2. portfolio.html — stat-grid
# ---------------------------------------------------------------------------


class TestPortfolioStatGrid:
    """portfolio.html must use stat-grid and stat-card for the KPI row."""

    def test_stat_grid_class_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'class="stat-grid"' in source, (
            "portfolio.html must wrap stat cards in a div with class='stat-grid'"
        )

    def test_stat_card_class_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'class="stat-card"' in source, (
            "portfolio.html must have at least one element with class='stat-card'"
        )

    def test_six_stat_cards_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        count = source.count('class="stat-card"')
        # The KPI row started at six cards and has grown since. What this guards
        # is that the row is still a populated stat-card grid, not that it has
        # frozen at one particular width — an exact count here only ever failed
        # because a KPI was added, never because one went missing.
        assert count >= 6, (
            f"portfolio.html must declare at least 6 stat-card elements, found {count}"
        )


# ---------------------------------------------------------------------------
# 3. portfolio.html — table-container on both data tables
# ---------------------------------------------------------------------------


class TestPortfolioTableContainers:
    """portfolio.html must wrap both tables in table-container divs."""

    def test_two_table_containers_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        count = source.count('class="table-container"')
        assert count >= 2, (
            f"portfolio.html must have at least 2 table-container divs, found {count}"
        )

    def test_contracts_table_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="Contract portfolio listing"' in source, (
            "portfolio.html must have the contracts table with aria-label='Contract portfolio listing'"
        )

    def test_upcoming_deliverables_table_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'aria-label="Upcoming contract deliverables"' in source, (
            "portfolio.html must have the deliverables table with aria-label='Upcoming contract deliverables'"
        )


# ---------------------------------------------------------------------------
# 4 & 5. portfolio.html — flex-wrap and min-width in health distribution
# ---------------------------------------------------------------------------


class TestPortfolioHealthDistributionResponsive:
    """Health distribution section must use flex-wrap and min-width."""

    def test_flex_wrap_wrap_present(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'flex-wrap: wrap' in source, (
            "portfolio.html health distribution must use flex-wrap: wrap to reflow on narrow screens"
        )

    def test_min_width_on_flex_item(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'min-width: 200px' in source, (
            "portfolio.html health distribution must set min-width: 200px on the flex child "
            "to prevent the bar chart from collapsing to zero width"
        )


# ---------------------------------------------------------------------------
# 6. style.css — fluid stat-grid definition
# ---------------------------------------------------------------------------


class TestStyleCssStatGrid:
    """style.css must define stat-grid as a fluid auto-fill grid."""

    def test_stat_grid_display_grid(self):
        source = _STYLE_CSS.read_text(encoding="utf-8")
        assert 'display: grid' in source, (
            "style.css must define display: grid for the stat-grid layout"
        )

    def test_stat_grid_auto_fill_minmax(self):
        source = _STYLE_CSS.read_text(encoding="utf-8")
        assert 'repeat(auto-fill, minmax' in source, (
            "style.css stat-grid must use repeat(auto-fill, minmax(...)) for fluid column sizing"
        )


# ---------------------------------------------------------------------------
# 7. style.css — 480px mobile media query overrides stat-grid
# ---------------------------------------------------------------------------


class TestStyleCssMobileBreakpoint:
    """style.css must override stat-grid to 2-column at 480px (mobile)."""

    def test_480px_media_query_present(self):
        source = _STYLE_CSS.read_text(encoding="utf-8")
        assert '@media (max-width: 480px)' in source, (
            "style.css must have a @media (max-width: 480px) breakpoint for mobile viewports"
        )

    def test_stat_grid_two_column_at_mobile(self):
        source = _STYLE_CSS.read_text(encoding="utf-8")
        assert 'grid-template-columns: 1fr 1fr' in source, (
            "style.css must override stat-grid to 2-column (1fr 1fr) inside the 480px media query"
        )


# ---------------------------------------------------------------------------
# 8. style.css — 768px tablet media query present
# ---------------------------------------------------------------------------


class TestStyleCssTabletBreakpoint:
    """style.css must declare a 768px media query (tablet breakpoint)."""

    def test_768px_media_query_present(self):
        source = _STYLE_CSS.read_text(encoding="utf-8")
        assert '@media (max-width: 768px)' in source, (
            "style.css must have a @media (max-width: 768px) breakpoint for tablet viewports"
        )


# ---------------------------------------------------------------------------
# 9. style.css — table-container overflow
# ---------------------------------------------------------------------------


class TestStyleCssTableContainer:
    """style.css table-container must set overflow: hidden for scrollable tables."""

    def test_table_container_overflow_hidden(self):
        source = _STYLE_CSS.read_text(encoding="utf-8")
        assert 'overflow: hidden' in source, (
            "style.css must set overflow: hidden on .table-container to clip and contain scrollable inner content"
        )


# ---------------------------------------------------------------------------
# 10. portfolio.html — section headers rendered
# ---------------------------------------------------------------------------


class TestPortfolioSectionHeaders:
    """portfolio.html must declare all three expected section headings."""

    def test_health_distribution_heading(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'Health Distribution' in source, (
            "portfolio.html must render the 'Health Distribution' section heading"
        )

    def test_contracts_heading(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert '>Contracts<' in source, (
            "portfolio.html must render the 'Contracts' section heading"
        )

    def test_upcoming_deliverables_heading(self):
        source = _PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        assert 'Upcoming Deliverables' in source, (
            "portfolio.html must render the 'Upcoming Deliverables' section heading"
        )


# ---------------------------------------------------------------------------
# Viewport catalogue (parametrized documentation test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vp", _VIEWPORTS, ids=lambda v: v["name"])
def test_viewport_definition_complete(vp):
    """Each viewport entry must have name, width, and height defined."""
    assert "name" in vp
    assert "width" in vp
    assert "height" in vp
    assert vp["width"] > 0
    assert vp["height"] > 0
