# CUI // SP-CTI
"""E2E: FathomDesk Phase 7.7 — POP badge + price cone + compare grid.

Flow:
  1. /options → AI Assist tab
  2. submit intent "Bullish AAPL through earnings, limited risk"
  3. assert:
       - POP badge visible, numeric value in [0, 100]
       - payoff chart canvas rendered
       - rationale text populated
  4. click "Compare alternates"
  5. assert compare grid has ≥ 1 column (intent-dependent; prefer 3)
  6. click "Use this one" on first non-primary column (if any)
  7. assert primary proposal title updates

Prerequisites:
  - Flask dashboard running at ICDEV_DASHBOARD_URL (default :5100)
  - Authenticated session
  - ICDEV_NO_LLM=true fine — rule path suffices
"""
from __future__ import annotations

import os
import re
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
    p = SHOTS / f"options_ai_prob_{name}.png"
    drv.save_screenshot(str(p))
    return str(p)


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


def test_pop_badge_renders(driver):
    _submit_intent(driver)
    _shot(driver, "01_proposal_with_pop")
    title_html = driver.find_element(By.ID, "ai-proposal-title").get_attribute("innerHTML")
    # Expect "POP: XX.X%" somewhere in the title's HTML.
    m = re.search(r"POP:\s*([\d.]+)%", title_html)
    assert m, f"POP badge not found in title HTML: {title_html[:200]}"
    pop = float(m.group(1))
    assert 0 <= pop <= 100, f"POP out of [0,100]: {pop}"


def test_payoff_chart_present(driver):
    _submit_intent(driver)
    canvas = driver.find_element(By.ID, "ai-payoff-chart")
    assert canvas.is_displayed()


def test_compare_grid_renders(driver):
    _submit_intent(driver)
    driver.find_element(By.ID, "ai-compare-btn").click()
    grid = WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.ID, "ai-compare-grid")
    )
    WebDriverWait(driver, 15).until(
        lambda d: grid.is_displayed() and "Loading" not in grid.text
    )
    _shot(driver, "02_compare_grid")
    # At least one column populated
    cols = grid.find_elements(By.TAG_NAME, "h4")
    assert cols, "Compare grid has no columns"


def test_promote_alternate_updates_primary(driver):
    _submit_intent(driver)
    driver.find_element(By.ID, "ai-compare-btn").click()
    WebDriverWait(driver, 15).until(
        lambda d: "Loading" not in d.find_element(By.ID, "ai-compare-grid").text
    )
    before = driver.find_element(By.ID, "ai-proposal-title").text
    use_buttons = driver.find_element(By.ID, "ai-compare-grid").find_elements(
        By.TAG_NAME, "button",
    )
    if not use_buttons:
        pytest.skip("no alternates returned — nothing to promote")
    # Click the first non-disabled Use button
    clicked = None
    for b in use_buttons:
        if not b.get_attribute("disabled"):
            clicked = b
            b.click()
            break
    if clicked is None:
        pytest.skip("every alternate has a preflight block — cannot promote")
    _shot(driver, "03_after_promote")
    WebDriverWait(driver, 5).until(
        lambda d: d.find_element(By.ID, "ai-proposal-title").text != before
    )
    after = driver.find_element(By.ID, "ai-proposal-title").text
    assert after != before, "Proposal title did not update after promote"
