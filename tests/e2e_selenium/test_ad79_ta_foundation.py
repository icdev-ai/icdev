# CUI // SP-CTI
"""E2E: FathomDesk — Technical Analysis Foundation (ad79-wrap-03).

Flow:
  1. Login via API key form at /login.
  2. Navigate to /analysis?ticker=AAPL.
  3. Wait for candlestick chart SVG to render inside #fd-chart-wrap.
  4. Assert candle chart renders (SVG with rect/line candle elements).
  5. Assert volume profile histogram renders on the right side.
  6. Assert at least one S/R line is visible.
  7. Assert swing/pattern markers are present in the SVG.
  8. Assert pattern summary badges are visible inside the chart.

Screenshots saved to playwright/screenshots/ta_*.png.

Prerequisites:
  - Flask/FathomDesk dashboard running at ICDEV_DASHBOARD_URL (default :5100)
  - ICDEV_DASHBOARD_API_KEY set, or server in open/dev mode
  - ICDEV_NO_LLM=true is acceptable
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5100")
API_KEY = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
SHOTS = _PROJECT_ROOT / "playwright" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

# Wait budget for chart render (API fetch + SVG draw)
CHART_TIMEOUT = 30


def _shot(drv, name: str) -> str:
    p = SHOTS / f"ta_{name}.png"
    drv.save_screenshot(str(p))
    return str(p)


def _login(driver) -> None:
    """Navigate to /login and submit the API key if the page requires auth."""
    driver.get(f"{BASE_URL}/login")
    try:
        key_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "api_key"))
        )
        key_input.clear()
        key_input.send_keys(API_KEY or "dev")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        # Wait until we're redirected away from /login
        WebDriverWait(driver, 10).until(
            lambda d: "/login" not in d.current_url
        )
    except TimeoutException:
        # Login form not present — session already active or auth disabled.
        pass


def _wait_for_chart_svg(driver) -> None:
    """Wait for the loading spinner to disappear and SVG to appear."""
    # The loading div is replaced by an <svg> element after the API fetch.
    WebDriverWait(driver, CHART_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#fd-chart-wrap svg"))
    )
    # Also confirm the stats block has been populated (chart data delivered).
    WebDriverWait(driver, CHART_TIMEOUT).until(
        lambda d: bool(
            d.execute_script("return document.getElementById('fd-stats').innerHTML.trim();")
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


@pytest.fixture(scope="module", autouse=True)
def analysis_page(driver):
    """Log in once and load /analysis?ticker=AAPL for the whole module."""
    _login(driver)
    driver.get(f"{BASE_URL}/analysis?ticker=AAPL")
    _wait_for_chart_svg(driver)
    _shot(driver, "00_loaded")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTAFoundation:
    """Technical Analysis Foundation smoke suite (ad79-wrap-03)."""

    def test_candle_chart_renders(self, driver):
        """SVG with candlestick rect/line elements exists inside #fd-chart-wrap."""
        svg = driver.find_element(By.CSS_SELECTOR, "#fd-chart-wrap svg")
        assert svg.is_displayed(), "#fd-chart-wrap SVG is not visible"

        # Candle bodies are <rect> elements; wicks are <line> elements.
        candle_rects = driver.execute_script(
            "return document.querySelectorAll('#fd-chart-wrap svg rect').length;"
        )
        candle_lines = driver.execute_script(
            "return document.querySelectorAll('#fd-chart-wrap svg line').length;"
        )
        assert candle_rects >= 10, (
            f"Expected ≥10 candle rect elements, got {candle_rects}"
        )
        assert candle_lines >= 10, (
            f"Expected ≥10 candle wick line elements, got {candle_lines}"
        )
        _shot(driver, "01_candle_chart")

    def test_volume_profile_renders_on_right(self, driver):
        """VP histogram bars (rect elements at vpLeft x-offset) are present."""
        # The VP divider line has a distinct opacity; VP rects start at vpLeft.
        # We check that the total rect count is substantially more than just
        # candle bodies (candles ≈ n bars; VP adds another bucket-count layer).
        total_rects = driver.execute_script(
            "return document.querySelectorAll('#fd-chart-wrap svg rect').length;"
        )
        # With 120 bars + ~20 VP buckets + value-area rect, expect well over 50.
        assert total_rects >= 50, (
            f"Volume profile missing: only {total_rects} rect elements in SVG"
        )

        # The VP divider line is rendered; verify via a line with opacity 0.4.
        vp_divider = driver.execute_script(
            "var lines = document.querySelectorAll('#fd-chart-wrap svg line');"
            "for (var i=0;i<lines.length;i++){"
            "  if (lines[i].getAttribute('opacity')==='0.4') return true;"
            "}"
            "return false;"
        )
        assert vp_divider, "VP divider line (opacity=0.4) not found in SVG"
        _shot(driver, "02_volume_profile")

    def test_sr_lines_visible(self, driver):
        """At least one support or resistance dashed line is drawn in the SVG."""
        sr_count = driver.execute_script(
            "var lines = document.querySelectorAll('#fd-chart-wrap svg line');"
            "var n = 0;"
            "for (var i=0;i<lines.length;i++){"
            "  var da = lines[i].getAttribute('stroke-dasharray');"
            "  var st = lines[i].getAttribute('stroke');"
            "  if (da && (st==='#dc3545' || st==='#28a745')) n++;"
            "}"
            "return n;"
        )
        assert sr_count >= 1, (
            "No S/R lines found — expected at least one dashed support or "
            "resistance line (stroke #dc3545 or #28a745). Got 0."
        )
        _shot(driver, "03_sr_lines")

    def test_swings_marked(self, driver):
        """Pattern-marker <g> elements (clickable swing annotations) are present."""
        marker_count = driver.execute_script(
            "var gs = document.querySelectorAll('#fd-chart-wrap svg g');"
            "var n = 0;"
            "for (var i=0;i<gs.length;i++){"
            "  if ((gs[i].getAttribute('style')||'').indexOf('cursor') >= 0) n++;"
            "}"
            "return n;"
        )
        # When no patterns are detected the chart is still valid; but for AAPL
        # 120 daily bars we generally expect at least one swing pattern.
        assert marker_count >= 1, (
            "No swing/pattern marker <g> elements found in SVG. "
            "Expected ≥1 cursor:pointer marker group for AAPL 120D chart."
        )
        _shot(driver, "04_swing_markers")

    def test_pattern_summary_visible(self, driver):
        """SVG badge-text elements summarising detected patterns are visible."""
        # Pattern badges render as <text> inside marker <g> elements.
        # Known badge strings from BADGE_TEXT map in the template.
        known_badges = ["DTop", "DBot", "TTop", "TBot", "R.Wdg", "F.Wdg"]
        badge_found = driver.execute_script(
            "var texts = document.querySelectorAll('#fd-chart-wrap svg text');"
            "var known = " + str(known_badges).replace("'", '"') + ";"
            "for (var i=0;i<texts.length;i++){"
            "  var t = texts[i].textContent || '';"
            "  for (var j=0;j<known.length;j++) {"
            "    if (t.indexOf(known[j]) >= 0) return t;"
            "  }"
            "}"
            "return null;"
        )
        assert badge_found is not None, (
            "No pattern summary badge text found in SVG. "
            "Expected at least one of: " + ", ".join(known_badges)
        )
        _shot(driver, "05_pattern_summary")
# CUI // SP-CTI
