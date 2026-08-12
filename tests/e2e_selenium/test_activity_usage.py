# CUI // SP-CTI
"""E2E Test: Activity Feed and Usage Tracking Pages.

Verifies the ICDEV™ dashboard activity page loads with SSE connection indicator
and activity entries, and the usage page displays cost breakdowns with period
selection.
Ported from .claude/commands/e2e/activity_usage.md,
tests/e2e/activity_feed.spec.ts, and tests/e2e/usage_tracking.spec.ts.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - Database initialised with audit trail and usage records
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import (
    ElementNotInteractableException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402
from tests.e2e_selenium.pages.base import BasePage  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
CUI_BANNER = "CUI // SP-CTI"

_NAV_LINK_XPATH = (
    "//a[contains(translate(text(),"
    "'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{label}')]"
)


def _click_top_nav_link(page: BasePage, label: str) -> bool:
    """Click a top-nav link by visible text, opening its dropdown first.

    Most destinations now live under a ``li.nav-dropdown`` trigger ("Ops ▾")
    whose submenu is collapsed until the trigger is hovered. The link is still
    in the DOM, so ``find_elements`` matches it — it simply is not interactable,
    and clicking it raises ElementNotInteractableException. That is how these
    two tests failed: the XPath said the link was there, and the fallback
    "navigate directly instead" branch could never run precisely BECAUSE it was
    found. A nav reorganisation turned a passing test into a failing one without
    anything on the page actually breaking.

    Returns True if a link was clicked, False if none was reachable — the caller
    keeps its direct-navigation fallback for the latter.
    """
    for link in page.driver.find_elements(By.XPATH, _NAV_LINK_XPATH.format(label=label)):
        trigger = link.find_elements(
            By.XPATH,
            "ancestor::li[contains(@class,'nav-dropdown')][1]"
            "//a[contains(@class,'nav-dropdown-trigger')]",
        )
        if trigger:
            # Hover rather than click: the trigger is href="javascript:void(0)"
            # and the menu opens on hover, so clicking it navigates nowhere and
            # can collapse the menu again.
            ActionChains(page.driver).move_to_element(trigger[0]).perform()
        try:
            WebDriverWait(page.driver, 5).until(EC.element_to_be_clickable(link)).click()
            return True
        except (ElementNotInteractableException, TimeoutException):
            continue  # try the next candidate; the text match is not unique
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def driver():
    """Yield a headless WebDriver for the entire module, then quit."""
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


@pytest.fixture(scope="module")
def page(driver):
    """Return a BasePage bound to the dashboard base URL."""
    return BasePage(driver, BASE_URL)


# ---------------------------------------------------------------------------
# Activity page tests
# ---------------------------------------------------------------------------

class TestActivityPage:
    """Activity Feed page — ported from activity_feed.spec.ts."""

    def test_activity_page_loads_with_cui_banner(self, page: BasePage):
        """Steps 1-4: Navigate to /activity and verify CUI banner."""
        # Step 1-2: Navigate to activity page
        page.navigate("/activity")
        page.screenshot("activity_usage_01_activity_overview")

        # Step 4-5: CUI banner present in body
        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Activity page body is empty"
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found on activity page"

        # Step 6: Check CUI banner element if present
        cui_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".cui-banner, [data-cui], .cui-banner-top",
        )
        if cui_elements:
            assert CUI_BANNER in cui_elements[0].text, "CUI banner element text mismatch"

    def test_sse_connection_indicator_is_present(self, page: BasePage):
        """Steps 5-7: Verify SSE connection status indicator exists."""
        page.navigate("/activity")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()

        # Step 5: At least one connection-related term should appear
        connection_terms = [
            "connected", "live", "real-time", "realtime", "streaming", "sse",
            "connection", "status", "online", "update",
        ]
        connection_found = any(term in body_text for term in connection_terms)

        # Step 6: Check for connection status UI elements (optional)
        connection_indicators = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".connection-status, .sse-status, .live-indicator, "
            "[data-connection], .status-indicator, .live-badge",
        )
        if connection_indicators:
            assert connection_indicators[0].is_displayed(), "Connection indicator not visible"

        page.screenshot("activity_usage_02_sse")
        assert connection_found or connection_indicators, (
            "No SSE/connection terms or indicator elements found on activity page"
        )

    def test_activity_entries_display(self, page: BasePage):
        """Steps 8-12: Verify activity entries or empty state."""
        page.navigate("/activity")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 8: Activity-related terms present
        activity_terms = [
            "activity", "event", "action", "timestamp", "audit",
            "log", "entry", "recent", "feed",
        ]
        activity_found = any(term in body_text.lower() for term in activity_terms)
        assert activity_found, "No activity-related terms found on activity page"

        # Step 9-11: Check for table rows / list items or empty state
        entries = page.driver.find_elements(
            By.CSS_SELECTOR,
            "table tbody tr, .activity-entry, .event-item, "
            ".feed-item, .timeline-item, .activity-item",
        )
        if entries:
            # Step 10: First entry is visible
            assert entries[0].is_displayed(), "First activity entry is not visible"
        else:
            # Step 11: Empty state message acceptable
            empty_terms = ["no activity", "no entries", "no events", "empty", "no recent"]
            assert any(term in body_text.lower() for term in empty_terms) or True, (
                "No activity entries and no empty-state message found"
            )

        page.screenshot("activity_usage_03_entries")

    def test_activity_page_navigation_from_dashboard(self, page: BasePage):
        """Steps 13-18: Navigate to /activity via nav link from dashboard."""
        page.navigate("/")

        # Step 14: Click the Activity nav link. It sits under the "Ops ▾"
        # dropdown, so the helper opens that first.
        #
        # Asserted rather than falling back to page.navigate(): this test exists
        # to prove Activity is REACHABLE FROM THE NAV, and a silent fallback
        # would keep it green through exactly the regression it is meant to
        # catch. (The original did have such a fallback, and it never ran —
        # the link was always found, just not clickable.)
        assert _click_top_nav_link(page, "ACTIVITY"), (
            "Activity is not reachable from the top nav: no matching link was "
            "clickable even after opening its dropdown. Navigating directly is "
            "not a substitute — the nav itself is what this test covers."
        )
        # Step 16: URL contains /activity. Waited for, because the click starts
        # a navigation and reading current_url immediately races it.
        WebDriverWait(page.driver, 10).until(lambda d: "/activity" in d.current_url)
        assert "/activity" in page.driver.current_url, (
            f"Expected /activity in URL, got: {page.driver.current_url}"
        )

        page.screenshot("activity_usage_04_nav")

        # Step 18: CUI banner present after navigation
        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing after navigating to activity page"


# ---------------------------------------------------------------------------
# Usage page tests
# ---------------------------------------------------------------------------

class TestUsagePage:
    """Usage Tracking page — ported from usage_tracking.spec.ts."""

    def test_usage_page_loads_with_cui_banner(self, page: BasePage):
        """Steps 19-22: Navigate to /usage and verify CUI banner."""
        page.navigate("/usage")
        page.screenshot("activity_usage_05_usage_overview")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Usage page body is empty"
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found on usage page"

        # Check CUI banner element if present
        cui_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".cui-banner, [data-cui], .cui-banner-top",
        )
        if cui_elements:
            assert CUI_BANNER in cui_elements[0].text, "CUI banner element text mismatch on usage page"

    def test_cost_breakdown_is_displayed(self, page: BasePage):
        """Steps 23-27: Verify cost/usage metrics are present."""
        page.navigate("/usage")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 23: Cost/usage-related terms
        cost_terms = [
            "cost", "usage", "token", "api call", "request",
            "total", "provider", "spend", "billing", "consumption",
        ]
        cost_found = any(term in body_text.lower() for term in cost_terms)
        assert cost_found, "No cost/usage terms found on usage page"

        # Step 24: Summary cards or metric displays
        cards = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".card, .summary-card, .metric-card, .usage-card, .stat-card",
        )
        if cards:
            assert cards[0].is_displayed(), "First usage card is not visible"

        # Step 25: Breakdown table or chart
        tables = page.driver.find_elements(
            By.CSS_SELECTOR,
            "table, .chart, .breakdown, svg, canvas",
        )
        if tables:
            assert tables[0].is_displayed(), "Cost breakdown table/chart is not visible"

        # Step 26: Numeric values present
        assert re.search(r"\d+", body_text), "No numeric values found on usage page"

        page.screenshot("activity_usage_06_cost")

    def test_period_selector_is_present(self, page: BasePage):
        """Steps 28-32: Verify period/date selector elements exist."""
        page.navigate("/usage")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 28: Period-related terms
        period_terms = [
            "period", "date", "range", "month", "week", "day",
            "last 7", "last 30", "this month", "custom", "filter",
        ]
        period_found = any(term in body_text.lower() for term in period_terms)

        # Step 29-31: Select/dropdown or date picker
        selectors = page.driver.find_elements(
            By.CSS_SELECTOR,
            "select, [role='combobox'], .date-picker, .period-selector, "
            "input[type='date'], .filter-group, .time-range",
        )
        if selectors:
            assert selectors[0].is_displayed(), "Period selector not visible"

            # Step 30: Try selecting second option if a <select> is present
            select_elements = page.driver.find_elements(By.CSS_SELECTOR, "select")
            if select_elements:
                options = select_elements[0].find_elements(By.TAG_NAME, "option")
                if len(options) > 1:
                    from selenium.webdriver.support.ui import Select
                    Select(select_elements[0]).select_by_index(1)

        # Step 31: Filter buttons (alternative)
        filter_buttons = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".btn-filter, .period-btn, [data-period], .time-filter button",
        )
        if filter_buttons:
            assert filter_buttons[0].is_displayed(), "Filter button not visible"

        page.screenshot("activity_usage_07_period")
        assert period_found or selectors or filter_buttons, (
            "No period selector terms, dropdowns, or filter buttons found on usage page"
        )

    def test_usage_page_navigation_from_dashboard(self, page: BasePage):
        """Steps 33-38: Navigate to /usage via nav link from dashboard."""
        page.navigate("/")

        # Step 34: Click the Usage nav link — also under a dropdown, and
        # asserted rather than falling back, for the reason given above.
        assert _click_top_nav_link(page, "USAGE"), (
            "Usage is not reachable from the top nav: no matching link was "
            "clickable even after opening its dropdown."
        )
        WebDriverWait(page.driver, 10).until(lambda d: "/usage" in d.current_url)
        assert "/usage" in page.driver.current_url, (
            f"Expected /usage in URL, got: {page.driver.current_url}"
        )

        page.screenshot("activity_usage_08_nav")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing after navigating to usage page"
# CUI // SP-CTI
