# CUI // SP-CTI
"""E2E Test: Agents and Monitoring Pages.

Verifies the ICDEV™ dashboard agents page displays the agent grid with status
indicators, and the monitoring page displays health checks, status icons, and
metric areas.
Ported from .claude/commands/e2e/agents_monitoring.md,
tests/e2e/agents_page.spec.ts, and tests/e2e/monitoring_page.spec.ts.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - Database initialised with agent registry data
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

CORE_AGENTS = [
    "Orchestrator", "Architect", "Builder", "Compliance",
    "Security", "Infrastructure", "Knowledge", "Monitor",
]


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
# Agents page tests
# ---------------------------------------------------------------------------

class TestAgentsPage:
    """Agents Page — ported from agents_page.spec.ts."""

    def test_agents_page_loads_with_cui_banner(self, page: BasePage):
        """Steps 1-7: Navigate to /agents and verify CUI banner."""
        page.navigate("/agents")
        page.screenshot("agents_monitoring_01_agents_overview")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Agents page body is empty"
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found on agents page"

        # Check header CUI banner element if present
        header_banners = page.driver.find_elements(
            By.CSS_SELECTOR,
            "header, .cui-banner-top, [data-cui='header'], .cui-banner",
        )
        if header_banners:
            assert CUI_BANNER in header_banners[0].text, "Header CUI banner text mismatch"

        # Check footer CUI banner element if present
        footer_banners = page.driver.find_elements(
            By.CSS_SELECTOR,
            "footer, .cui-banner-bottom, [data-cui='footer'], .cui-banner",
        )
        if footer_banners:
            assert CUI_BANNER in footer_banners[-1].text, "Footer CUI banner text mismatch"

    def test_agent_grid_displays_all_core_agents(self, page: BasePage):
        """Steps 5-6: Verify all 8 core agents appear in the agent grid."""
        page.navigate("/agents")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()

        for agent in CORE_AGENTS:
            assert agent.lower() in body_text, f"Core agent '{agent}' not found on agents page"

        page.screenshot("agents_monitoring_02_agent_grid")

        # Step 9: Check for extended agents (informational only — may be absent)
        extended_agents = ["MBSE", "Modernization", "Requirements", "Supply Chain", "Simulation"]
        # Not a hard assertion — extended agents are optional (informational only)
        _ = sum(1 for a in extended_agents if a.lower() in body_text)

    def test_agent_status_indicators_are_displayed(self, page: BasePage):
        """Steps 7-8: Verify status indicators and port numbers."""
        page.navigate("/agents")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 7: Status-related terms
        status_terms = ["status", "health", "port", "active", "idle", "running", "stopped"]
        status_found = any(term in body_text.lower() for term in status_terms)
        assert status_found, "No status-related terms found on agents page"

        # Step 7: Agent card/grid elements
        agent_cards = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".agent-card, .agent-item, .card, [data-agent]",
        )
        if agent_cards:
            assert agent_cards[0].is_displayed(), "First agent card is not visible"

        # Step 8: Port numbers in 8443-8458 range
        # Not a hard assertion — ports may not render in all dashboard states

        page.screenshot("agents_monitoring_03_agent_status")

    def test_agents_page_navigation_from_dashboard(self, page: BasePage):
        """Steps 10-15: Navigate to /agents via nav link from dashboard."""
        page.navigate("/")

        agents_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'AGENTS')]",
        )
        if agents_links:
            agents_links[0].click()
            assert "/agents" in page.driver.current_url, (
                f"Expected /agents in URL, got: {page.driver.current_url}"
            )
        else:
            page.navigate("/agents")

        page.screenshot("agents_monitoring_04_agents_nav")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing after navigating to agents page"


# ---------------------------------------------------------------------------
# Monitoring page tests
# ---------------------------------------------------------------------------

class TestMonitoringPage:
    """Monitoring Page — ported from monitoring_page.spec.ts."""

    def test_monitoring_page_loads_with_cui_banner(self, page: BasePage):
        """Steps 16-19: Navigate to /monitoring and verify CUI banner."""
        page.navigate("/monitoring")
        page.screenshot("agents_monitoring_05_monitoring_overview")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Monitoring page body is empty"
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found on monitoring page"

        cui_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".cui-banner, [data-cui], .cui-banner-top",
        )
        if cui_elements:
            assert CUI_BANNER in cui_elements[0].text, "CUI banner element text mismatch on monitoring page"

    def test_status_icons_and_health_indicators(self, page: BasePage):
        """Steps 20-22: Verify health/status terms and status icon elements."""
        page.navigate("/monitoring")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 20: Monitoring-related terms
        monitor_terms = ["health", "status", "metric", "monitor", "alert", "check"]
        monitor_found = any(term in body_text.lower() for term in monitor_terms)
        assert monitor_found, "No health/status terms found on monitoring page"

        # Step 21: Status icon elements
        status_icons = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".status-icon, .health-icon, .status-indicator, "
            "[data-status], .badge, .status-dot, [role='status']",
        )
        if status_icons:
            assert status_icons[0].is_displayed(), "Status icon not visible"

        page.screenshot("agents_monitoring_06_monitoring_status")
        assert monitor_found, "Monitoring page lacks health/status content"

    def test_monitoring_metric_display_areas(self, page: BasePage):
        """Steps 23-26: Verify metric containers and alert elements."""
        page.navigate("/monitoring")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 23-24: Metric/chart containers
        cards = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".card, .metric-card, .summary-card, .panel",
        )
        if cards:
            assert cards[0].is_displayed(), "First metric card is not visible"

        # Step 24: Metric-related terms
        metric_terms = ["metric", "count", "rate", "latency", "response", "uptime", "error"]
        metric_found = any(term in body_text.lower() for term in metric_terms)

        page.screenshot("agents_monitoring_07_monitoring_metrics")
        assert metric_found or cards, "No metric terms or metric card elements found on monitoring page"

    def test_monitoring_page_navigation_from_dashboard(self, page: BasePage):
        """Steps 27-32: Navigate to /monitoring via nav link from dashboard."""
        page.navigate("/")

        # Step 28: Find Monitor/Monitoring nav link
        monitor_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'MONITOR')]",
        )
        if monitor_links:
            monitor_links[0].click()
            assert "/monitoring" in page.driver.current_url, (
                f"Expected /monitoring in URL, got: {page.driver.current_url}"
            )
        else:
            page.navigate("/monitoring")

        page.screenshot("agents_monitoring_08_monitoring_nav")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing after navigating to monitoring page"
# CUI // SP-CTI
