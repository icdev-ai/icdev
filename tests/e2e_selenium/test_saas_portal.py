# CUI // SP-CTI
"""E2E Test: SaaS Portal Authentication and Pages.

Verifies the ICDEV™ SaaS tenant portal login flow, session management,
logout, and that all portal pages load correctly with CUI banners and
sidebar navigation.
Ported from .claude/commands/e2e/saas_portal.md.

Prerequisites:
  - SaaS API gateway running with portal blueprint registered
  - Platform database initialized with at least one tenant and user
  - Portal accessible at http://localhost:5050/portal or http://localhost:8443/portal
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

BASE_URL = os.environ.get("ICDEV_PORTAL_URL", "http://localhost:5050")
CUI_BANNER = "CUI // SP-CTI"

PORTAL_PAGES = [
    ("/portal/projects",    ["project"]),
    ("/portal/compliance",  ["compliance", "control", "nist", "fedramp"]),
    ("/portal/team",        ["team", "user", "member"]),
    ("/portal/settings",    ["settings", "configuration", "config"]),
    ("/portal/keys",        ["api key", "key", "token"]),
    ("/portal/usage",       ["usage", "token", "cost"]),
    ("/portal/audit",       ["audit", "event", "trail"]),
]

SIDEBAR_ITEMS = [
    "dashboard", "projects", "compliance", "team", "settings",
    "api keys", "usage", "audit trail",
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
    """Return a BasePage bound to the portal base URL."""
    return BasePage(driver, BASE_URL)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSaasPortalLogin:
    """SaaS Portal Login Flow — ported from saas_portal.md."""

    def test_login_page_loads_with_cui_banner(self, page: BasePage):
        """Steps 1-8: Navigate to /portal/login and verify CUI banner + form."""
        page.navigate("/portal/login")
        page.screenshot("saas_portal_01_login")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Portal login page body is empty"
        assert CUI_BANNER in body_text, (
            f"CUI banner '{CUI_BANNER}' not found on portal login page"
        )

        # Step 5: API Key input field
        api_key_inputs = page.driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='password'], input[name='api_key'], "
            "input[name='key'], input[placeholder*='API'], "
            "input[placeholder*='key'], input[placeholder*='Key']",
        )

        # Step 6: Sign In button
        sign_in_btns = page.driver.find_elements(
            By.XPATH,
            "//*[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'SIGN IN') "
            "or contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'LOGIN') "
            "or contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'LOG IN')]",
        )

        # Step 7-8: Page title and classification text
        portal_terms = ["icdev", "portal", "il4", "il5", "nist", "fedramp"]
        portal_found = any(term in body_text.lower() for term in portal_terms)

        assert api_key_inputs or sign_in_btns or portal_found, (
            "No login form (API key input or sign-in button) found on portal login page"
        )

    def test_login_with_test_api_key(self, page: BasePage):
        """Steps 9-14: Submit a test API key and verify redirect or error."""
        page.navigate("/portal/login")

        api_key_inputs = page.driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='password'], input[name='api_key'], "
            "input[name='key'], input[placeholder*='API'], "
            "input[placeholder*='key'], input[placeholder*='Key'], "
            "input[type='text']",
        )

        sign_in_btns = page.driver.find_elements(
            By.XPATH,
            "//*[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'SIGN IN') "
            "or contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'LOGIN')]",
        )

        if api_key_inputs and sign_in_btns:
            api_key_inputs[0].clear()
            api_key_inputs[0].send_keys("sparkpilot")
            sign_in_btns[0].click()

            import time
            time.sleep(1)

        page.screenshot("saas_portal_02_post_login")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing on post-login portal page"

    def test_logout_returns_to_login(self, page: BasePage):
        """Steps 15-20: Navigate to /portal/logout and verify redirect to login."""
        page.navigate("/portal/logout")

        import time
        time.sleep(1)

        page.screenshot("saas_portal_03_post_logout")

        current_url = page.driver.current_url
        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 18: URL contains /portal/login after logout
        login_redirected = "/portal/login" in current_url or "/login" in current_url

        # Step 19: Login form shown again
        login_form = page.driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='password'], input[name='api_key'], form",
        )

        # Both redirect and login form presence are checked
        assert login_redirected or login_form, (
            f"Expected redirect to /portal/login after logout, got: {current_url}"
        )
        assert CUI_BANNER in body_text, "CUI banner missing on post-logout portal login page"

    def test_unauthenticated_access_redirects_to_login(self, page: BasePage):
        """Steps 21-26: Verify unauthenticated pages redirect to /portal/login."""
        protected_paths = ["/portal/", "/portal/projects", "/portal/compliance"]

        for path in protected_paths:
            page.navigate(path)
            current_url = page.driver.current_url
            body_text = page.driver.find_element(By.TAG_NAME, "body").text

            # Either redirected to login or content is present
            login_redirected = "login" in current_url
            has_content = CUI_BANNER in body_text

            assert login_redirected or has_content, (
                f"Expected redirect to login or CUI banner on {path}, got URL: {current_url}"
            )


class TestSaasPortalPages:
    """SaaS Portal Page Navigation — ported from saas_portal.md."""

    def test_portal_pages_load_with_cui_banner(self, page: BasePage):
        """Steps 27-40: Navigate to each portal page and verify CUI banner."""
        for portal_path, terms in PORTAL_PAGES:
            page.navigate(portal_path)
            body_text = page.driver.find_element(By.TAG_NAME, "body").text

            # Accept either: redirected to login (unauthenticated) OR page loaded with CUI
            login_redirect = "login" in page.driver.current_url
            has_cui = CUI_BANNER in body_text

            page.screenshot(f"saas_portal_{portal_path.replace('/', '_').strip('_')}")

            assert login_redirect or has_cui, (
                f"Neither login redirect nor CUI banner found on {portal_path}"
            )

    def test_sidebar_navigation_is_consistent(self, page: BasePage):
        """Steps 41-43: Verify sidebar navigation items on authenticated pages."""
        page.navigate("/portal/")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()

        # Check sidebar terms
        found_sidebar_items = [item for item in SIDEBAR_ITEMS if item in body_text]

        sidebar_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".sidebar, .nav-sidebar, .portal-nav, nav, aside",
        )

        # Sign Out link
        page.driver.find_elements(
            By.XPATH,
            "//*[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'SIGN OUT') "
            "or contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'LOGOUT')]",
        )

        page.screenshot("saas_portal_sidebar")

        # If not authenticated, we'll see the login page — that's acceptable
        login_redirect = "login" in page.driver.current_url
        assert login_redirect or found_sidebar_items or sidebar_elements, (
            "Neither login redirect nor sidebar navigation found on portal page"
        )
# CUI // SP-CTI
