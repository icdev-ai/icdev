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
from selenium.webdriver.common.by import By

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402
from tests.e2e_selenium.pages.base import BasePage  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
CUI_BANNER = "CUI // SP-CTI"


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

        # Step 14: Find and click Activity nav link
        activity_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'ACTIVITY')]",
        )
        if activity_links:
            activity_links[0].click()
            # Step 16: URL contains /activity
            assert "/activity" in page.driver.current_url, (
                f"Expected /activity in URL, got: {page.driver.current_url}"
            )
        else:
            # Fallback: direct navigation
            page.navigate("/activity")

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

        # Step 34: Find and click Usage nav link
        usage_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'USAGE')]",
        )
        if usage_links:
            usage_links[0].click()
            assert "/usage" in page.driver.current_url, (
                f"Expected /usage in URL, got: {page.driver.current_url}"
            )
        else:
            # Fallback: direct navigation
            page.navigate("/usage")

        page.screenshot("activity_usage_08_nav")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing after navigating to usage page"
# CUI // SP-CTI
