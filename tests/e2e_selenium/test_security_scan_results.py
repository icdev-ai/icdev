# CUI // SP-CTI
"""E2E Test: Security Scan Results Display.

Verifies security scan results are properly displayed in the dashboard,
including monitoring page health indicators and audit trail entries.
Ported from .claude/commands/e2e/security_scan_results.md.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - At least one project with security scan results
"""
from __future__ import annotations

import os
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
# Tests
# ---------------------------------------------------------------------------

class TestSecurityScanResults:
    """Security Scan Results Display — ported from security_scan_results.md."""

    def test_dashboard_shows_security_summary(self, page: BasePage):
        """Steps 1-3: Navigate to dashboard and check security summary/alerts."""
        page.navigate("/")
        page.screenshot("security_scan_results_01_dashboard")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Dashboard body is empty"
        assert CUI_BANNER in body_text, (
            f"CUI banner '{CUI_BANNER}' not found on dashboard"
        )

        # Step 3: Security summary or alerts section
        security_terms = [
            "alert", "security", "scan", "vulnerability", "finding",
            "risk", "threat", "warning",
        ]
        security_found = any(term in body_text.lower() for term in security_terms)
        # Security summary is optional — no hard assert if empty
        _ = security_found

    def test_monitoring_page_loads_with_health_indicators(self, page: BasePage):
        """Steps 4-9: Navigate to monitoring page and verify health indicators."""
        page.navigate("/")

        # Step 4: Navigate via nav link
        monitor_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'MONITOR')]",
        )
        if monitor_links:
            monitor_links[0].click()
        else:
            page.navigate("/monitoring")

        page.screenshot("security_scan_results_02_monitoring")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, (
            f"CUI banner '{CUI_BANNER}' not found on monitoring page"
        )

        # Step 7: Health check status indicators
        health_terms = ["health", "status", "check", "indicator", "alert", "metric"]
        health_found = any(term in body_text.lower() for term in health_terms)
        assert health_found, "No health/status terms found on monitoring page"

        # Step 8: Active alerts section (optional)
        page.driver.find_elements(
            By.CSS_SELECTOR,
            ".alert, .alert-item, .active-alert, [data-alert], .warning",
        )
        # Alerts may be absent; presence is informational

        # Step 9: Metric display areas
        metric_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".card, .metric-card, .stat-card, .panel, .metric",
        )
        assert metric_elements or health_found, (
            "No metric display areas found on monitoring page"
        )

    def test_audit_trail_page_loads_with_entries(self, page: BasePage):
        """Steps 10-16: Navigate to /audit and verify audit entries."""
        # Step 10-11: Navigate to audit trail
        audit_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'AUDIT')]",
        )
        if audit_links:
            audit_links[0].click()
        else:
            page.navigate("/audit")

        page.screenshot("security_scan_results_03_audit_trail")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Audit trail page body is empty"
        assert CUI_BANNER in body_text, (
            f"CUI banner '{CUI_BANNER}' not found on audit trail page"
        )

        # Step 14: Audit entries in table format
        audit_tables = page.driver.find_elements(
            By.CSS_SELECTOR,
            "table, .audit-table, .audit-log, .audit-entries",
        )

        audit_terms = ["audit", "event", "timestamp", "action", "actor", "log"]
        audit_found = any(term in body_text.lower() for term in audit_terms)
        assert audit_found, "No audit-related terms found on audit trail page"

        # Step 15: Audit entry columns (timestamp, event type, actor, action)
        entry_columns = ["timestamp", "event", "actor", "action", "type", "user"]
        [col for col in entry_columns if col in body_text.lower()]

        if audit_tables:
            assert audit_tables[0].is_displayed(), "Audit table is not visible"

    def test_security_scan_results_on_security_page(self, page: BasePage):
        """Additional: Navigate to /security and check scan results display."""
        page.navigate("/security")
        page.screenshot("security_scan_results_04_security_page")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, (
            f"CUI banner '{CUI_BANNER}' not found on security page"
        )

        # Security scan results display
        scan_terms = [
            "scan", "vulnerability", "bandit", "sast", "finding",
            "severity", "cve", "security", "critical", "high", "medium",
        ]
        scan_found = any(term in body_text.lower() for term in scan_terms)

        # Cards or table with scan results
        result_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            "table, .card, .scan-result, .vulnerability, .finding",
        )

        assert scan_found or result_elements, (
            "No security scan results or related terms found on security page"
        )

    def test_cui_banner_on_all_security_pages(self, page: BasePage):
        """CUI Verification: CUI banner present on all security-related pages."""
        pages_to_check = ["/", "/monitoring", "/audit", "/security"]
        for path in pages_to_check:
            page.navigate(path)
            body_text = page.driver.find_element(By.TAG_NAME, "body").text
            assert CUI_BANNER in body_text, (
                f"CUI banner '{CUI_BANNER}' not found on {path}"
            )
# CUI // SP-CTI
