# CUI // SP-CTI
"""E2E: FathomDesk Phase 7.8 — time-slider, portfolio greeks, share URL.

Flow:
  A. /options → AI Assist → submit intent → assert time-slider renders;
     drag slider; assert chart dataset changes.
  B. /portfolio → assert Portfolio Greeks card exists (may auto-hide
     if user has no option positions; test passes either way).
  C. /options AI Assist → click 🔗 Share → assert status message shows
     success; extract URL via JS clipboard read (or fallback check).

Prereqs:
  - Flask dashboard at ICDEV_DASHBOARD_URL (default :5100)
  - Auth session
  - ICDEV_NO_LLM=true is fine
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from selenium.common.exceptions import TimeoutException  # noqa: E402
from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5100")
TRADING_EMAIL = os.environ.get("FATHOMDESK_EMAIL", "sovanna.chuon@gmail.com")
TRADING_PASSWORD = os.environ.get("FATHOMDESK_PASSWORD", "FathomDesk2026!")
SHOTS = _PROJECT_ROOT / "playwright" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


def _login(driver) -> None:
    """Log into the FathomDesk trading dashboard via email/password form."""
    driver.get(f"{BASE_URL}/login")
    try:
        email_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "login-email"))
        )
        email_input.clear()
        email_input.send_keys(TRADING_EMAIL)
        pw_input = driver.find_element(By.ID, "login-password")
        pw_input.clear()
        pw_input.send_keys(TRADING_PASSWORD)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        WebDriverWait(driver, 10).until(
            lambda d: "/login" not in d.current_url
        )
    except TimeoutException:
        pass  # Already authenticated or auth disabled


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1600, 1000))
    drv.implicitly_wait(5)
    _login(drv)
    yield drv
    drv.quit()


def _shot(drv, name: str):
    p = SHOTS / f"options_ai_greeks_{name}.png"
    drv.save_screenshot(str(p))


def _submit_intent(driver):
    driver.get(f"{BASE_URL}/options")
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "tab-aiassist-btn"))
    ).click()
    ta = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "ai-intent-text"))
    )
    ta.clear()
    ta.send_keys("Bullish AAPL through earnings, limited risk")
    driver.find_element(By.ID, "ai-intent-underlying").send_keys("AAPL")
    driver.find_element(By.ID, "ai-intent-submit").click()
    WebDriverWait(driver, 30).until(
        lambda d: d.find_element(By.ID, "ai-proposal-card").is_displayed()
    )


def test_time_slider_renders(driver):
    _submit_intent(driver)
    slider_wrap = driver.find_element(By.ID, "ai-time-slider-wrap")
    # Expect the wrap to be visible when frames are returned.
    assert slider_wrap.is_displayed(), "Time slider wrap hidden despite frames"
    _shot(driver, "01_slider_visible")


def test_time_slider_changes_chart(driver):
    _submit_intent(driver)
    # Read current chart data length via JS.
    before = driver.execute_script(
        "return aiPayoffChart.data.datasets[0].data.slice();"
    )
    slider = driver.find_element(By.ID, "ai-time-slider")
    # Drag to value 0 (today / max DTE remaining).
    driver.execute_script(
        "arguments[0].value = 0; arguments[0].dispatchEvent(new Event('input'));",
        slider,
    )
    after = driver.execute_script(
        "return aiPayoffChart.data.datasets[0].data.slice();"
    )
    _shot(driver, "02_slider_moved")
    # At least one data point should differ (midpoint P&L shifts with DTE).
    diff = sum(1 for a, b in zip(before, after) if abs((a or 0) - (b or 0)) > 0.01)
    assert diff > 0, "Chart data didn't change after slider drag"


def test_portfolio_greeks_card_loads(driver):
    driver.get(f"{BASE_URL}/portfolio")
    # Card exists in DOM regardless; may be display:none when empty.
    card = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "pf-greeks-card"))
    )
    # If displayed, it must carry 4 greek values.
    if card.is_displayed():
        for gid in ("pfg-delta", "pfg-gamma", "pfg-theta", "pfg-vega"):
            el = driver.find_element(By.ID, gid)
            assert el.text.strip(), f"{gid} empty while card visible"
    _shot(driver, "03_portfolio_greeks")


def test_share_button_produces_url(driver):
    _submit_intent(driver)
    # Click Share; we can't read the clipboard in headless Chrome reliably,
    # but we CAN observe the execute-status span flips to success color.
    driver.find_element(By.ID, "ai-share-btn").click()
    status = driver.find_element(By.ID, "ai-execute-status")
    # Allow up to 5s for the fetch + clipboard.
    WebDriverWait(driver, 5).until(
        lambda d: status.text and ("Link copied" in status.text
                                    or "Share URL ready" in status.text
                                    or "failed" in status.text.lower())
    )
    _shot(driver, "04_share_status")
    # Success means no "failed" substring.
    assert "failed" not in status.text.lower(), \
        f"Share button status shows failure: {status.text}"
