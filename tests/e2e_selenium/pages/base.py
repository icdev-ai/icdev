"""Base page object for Selenium E2E tests.

Provides common driver interactions shared by all page objects:
  - navigate(path)              — load base_url + path
  - wait_for_element(selector)  — wait for a CSS selector, return element
  - screenshot(name)            — save playwright/screenshots/<name>.png
"""
from __future__ import annotations

from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# playwright/screenshots/ lives at the project root (4 levels up from this file)
_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "playwright" / "screenshots"


class BasePage:
    """Minimal base class for page objects.

    Args:
        driver:   A live Selenium WebDriver instance.
        base_url: Root URL of the application (e.g. "http://localhost:5050").
    """

    def __init__(self, driver: WebDriver, base_url: str) -> None:
        self.driver = driver
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, path: str) -> None:
        """Navigate to *base_url + path*.

        Args:
            path: URL path to append, e.g. "/dashboard".
        """
        self.driver.get(f"{self.base_url}/{path.lstrip('/')}")
        self._suppress_tour()

    def _suppress_tour(self) -> None:
        """Permanently suppress the ICDEV tour overlay via localStorage and direct DOM removal."""
        try:
            self.driver.execute_script(
                "localStorage.setItem('icdev_tour_completed', '1');"
                "localStorage.setItem('icdev_tour_last_step', '999');"
                "var el = document.getElementById('icdev-tour-welcome');"
                "if (el) { el.classList.remove('visible'); el.style.display='none'; el.remove(); }"
                "document.querySelectorAll('[class*=\"tour\"]').forEach(function(e){"
                "  e.style.display='none'; e.remove();"
                "});"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Element helpers
    # ------------------------------------------------------------------

    def wait_for_element(self, selector: str, timeout: int = 10) -> WebElement:
        """Wait for a CSS selector to be visible and return the element.

        Args:
            selector: CSS selector string (e.g. "#main-nav", ".card").
            timeout:  Maximum seconds to wait (default: 10).

        Returns:
            The first matching visible WebElement.

        Raises:
            selenium.common.exceptions.TimeoutException: if not found in time.
        """
        return WebDriverWait(self.driver, timeout).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
        )

    # ------------------------------------------------------------------
    # Screenshots  (CLAUDE.md guardrail: playwright/screenshots/<name>.png)
    # ------------------------------------------------------------------

    def screenshot(self, name: str) -> str:
        """Capture a screenshot to playwright/screenshots/<name>.png.

        The directory is created on first use.

        Args:
            name: Base filename (without extension), e.g. "login-page".

        Returns:
            Absolute path string of the saved file.
        """
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = _SCREENSHOT_DIR / f"{name}.png"
        self.driver.save_screenshot(str(path))
        return str(path)
