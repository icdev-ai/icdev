# CUI // SP-CTI
"""E2E: FathomDesk Phase 7.6 — AI Options Assist tab.

Exercises the full hybrid flow:
  1. open /options
  2. click 'AI Assist' tab
  3. submit an intent ('Bullish AAPL through earnings, limited risk')
  4. assert proposal modal renders: strategy label, legs table, payoff chart
  5. capture screenshots

The execute step is NOT performed (requires an active sandbox attempt +
a graduated user) — wrap-04 focuses on the propose → modal path. The
executeability signal is asserted via the presence / enabled-state of
the Execute button, not by clicking it.

Prerequisites:
  - Flask dashboard running at ICDEV_DASHBOARD_URL
    (default http://localhost:5100 for FathomDesk)
  - An authenticated session (cookie) or login flow handled upstream.
  - ICDEV_NO_LLM=true is fine — the rule-fallback path produces a
    valid proposal on its own.
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

from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5100")
SHOTS = _PROJECT_ROOT / "playwright" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1600, 1000))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


def _shot(drv, name: str) -> str:
    p = SHOTS / f"options_ai_{name}.png"
    drv.save_screenshot(str(p))
    return str(p)


def test_ai_assist_tab_renders(driver):
    driver.get(f"{BASE_URL}/options")
    _shot(driver, "01_options_page")
    # Tab button exists
    btn = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "tab-aiassist-btn"))
    )
    btn.click()
    # AI panel visible
    panel = driver.find_element(By.ID, "tab-aiassist")
    assert panel.is_displayed(), "AI Assist tab did not display after click"
    _shot(driver, "02_ai_tab_empty")


def test_ai_assist_proposal_flow(driver):
    """Submit an intent and assert the proposal modal renders."""
    driver.get(f"{BASE_URL}/options")
    driver.find_element(By.ID, "tab-aiassist-btn").click()
    textarea = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "ai-intent-text"))
    )
    textarea.clear()
    textarea.send_keys("Bullish AAPL through earnings, limited risk")
    driver.find_element(By.ID, "ai-intent-underlying").send_keys("AAPL")
    driver.find_element(By.ID, "ai-intent-submit").click()

    # Wait up to 30s for the proposal card to become visible
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.find_element(By.ID, "ai-proposal-card").is_displayed()
        )
    except Exception:
        _shot(driver, "03_fail_no_card")
        pytest.fail("Proposal card did not render within 30s")

    _shot(driver, "03_proposal_modal")

    # Title + legs table populated
    title = driver.find_element(By.ID, "ai-proposal-title").text
    assert title and title != "—", f"Proposal title not populated: {title!r}"
    legs_body = driver.find_element(By.ID, "ai-legs-body")
    assert legs_body.find_elements(By.TAG_NAME, "tr"), "Legs table empty"

    # Payoff chart canvas is present
    canvas = driver.find_element(By.ID, "ai-payoff-chart")
    assert canvas.is_displayed(), "Payoff canvas not displayed"
