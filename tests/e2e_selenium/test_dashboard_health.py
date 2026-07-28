# CUI // SP-CTI
"""E2E Test: Dashboard Health Check.

Verifies the ICDEV™ web dashboard loads correctly with CUI banners and core navigation.
Ported from .claude/commands/e2e/dashboard_health.md and tests/e2e/dashboard_health.spec.ts.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - Database initialised with at least one project
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

class TestDashboardHealthCheck:
    """Dashboard Health Check — ported from dashboard_health.spec.ts."""

    def test_dashboard_loads_with_cui_banners(self, page: BasePage):
        """Steps 1-6: Navigate to root, verify title and CUI banners."""
        # Step 1-3: Navigate and verify page loads
        page.navigate("/")

        # Step 4: Screenshot the full dashboard
        page.screenshot("dashboard_health_01_dashboard")

        # Step 5-6: Verify CUI banners (at least once in body text)
        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found in page body"

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

    def test_navigation_links_are_functional(self, page: BasePage):
        """Steps 7-12: Verify navigation links and Projects page."""
        page.navigate("/")

        # Step 7: Check expected nav links are visible when present
        nav_link_texts = ["Projects", "Agents", "Compliance", "Security", "Monitoring", "Audit"]
        for link_text in nav_link_texts:
            links = page.driver.find_elements(
                By.XPATH,
                f"//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{link_text.upper()}')]",
            )
            if links:
                assert links[0].is_displayed(), f"Nav link '{link_text}' is not visible"

        # Step 8-12: Click Projects link if present
        projects_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'PROJECTS')]",
        )
        if projects_links:
            projects_links[0].click()

            # Step 10: Screenshot projects page
            page.screenshot("dashboard_health_02_projects")

            # Step 11-12: Verify projects page content and CUI banner
            body_text = page.driver.find_element(By.TAG_NAME, "body").text
            assert CUI_BANNER in body_text, "CUI banner missing on Projects page"

    def test_agents_page_displays_agent_grid(self, page: BasePage):
        """Steps 13-17: Navigate to Agents page, verify 8-agent grid."""
        page.navigate("/")

        # Step 14-16: Click Agents nav link if present
        agents_links = page.driver.find_elements(
            By.XPATH,
            "//a[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'AGENTS')]",
        )
        if agents_links:
            agents_links[0].click()
        else:
            page.navigate("/agents")

        page.screenshot("dashboard_health_03_agents")

        # Step 17: Verify all 8 core agent names appear in body
        agent_names = [
            "Orchestrator", "Architect", "Builder", "Compliance",
            "Security", "Infrastructure", "Knowledge", "Monitor",
        ]
        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()
        for agent in agent_names:
            assert agent.lower() in body_text, f"Agent '{agent}' not found on agents page"
# CUI // SP-CTI
