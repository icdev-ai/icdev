# CUI // SP-CTI
"""E2E: FathomDesk Phase 7.8 — share URL round-trip for AI Assist proposal.

Flow:
  1. Navigate to /options → click AI Assist tab → submit an intent.
  2. Capture the proposed strategy name from ai-proposal-title.
  3. Intercept the share URL by patching window.fetch before clicking Share.
  4. Click 🔗 Share → extract the aiproposal= URL from JS state.
  5. Open the URL in a new tab.
  6. The page's auto-load IIFE decodes the token + re-submits the proposal.
  7. Assert ai-proposal-title is non-empty and contains the strategy name.

Prereqs:
  - Flask trading dashboard at ICDEV_DASHBOARD_URL (default :5100)
  - Auth session via /login
  - ICDEV_NO_LLM=true is fine (rule-based fallback path)
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

# Deterministic intent — underlying + direction known so rule-based path fires.
_INTENT_TEXT = "Bullish AAPL through earnings, limited risk"
_INTENT_UNDERLYING = "AAPL"


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


def _shot(drv, name: str) -> None:
    p = SHOTS / f"fathomdesk_78_share_{name}.png"
    drv.save_screenshot(str(p))


def _submit_intent(driver) -> None:
    """Navigate to /options AI Assist tab and submit an intent until proposal card shows."""
    driver.get(f"{BASE_URL}/options")
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "tab-aiassist-btn"))
    ).click()
    ta = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "ai-intent-text"))
    )
    ta.clear()
    ta.send_keys(_INTENT_TEXT)
    underlying = driver.find_element(By.ID, "ai-intent-underlying")
    underlying.clear()
    underlying.send_keys(_INTENT_UNDERLYING)
    driver.find_element(By.ID, "ai-intent-submit").click()
    WebDriverWait(driver, 30).until(
        lambda d: d.find_element(By.ID, "ai-proposal-card").is_displayed()
    )


def test_share_url_round_trip(driver):
    """Share URL encodes the proposal; navigating to it restores strategy name in DOM."""
    _submit_intent(driver)

    # Capture the strategy name before sharing.
    # ai-proposal-title renders as "<strategy_id> on <underlying>".
    title_el = driver.find_element(By.ID, "ai-proposal-title")
    original_title = title_el.text.strip()
    assert original_title and original_title != "—", \
        "ai-proposal-title must be populated before testing share round-trip"
    strategy_name = original_title.split(" on ")[0].strip() if " on " in original_title else original_title.split()[0]

    _shot(driver, "01_proposal_before_share")

    # Patch window.fetch so we can intercept the /api/options/ai-assist/share
    # response and grab j.url without relying on clipboard access (which is
    # blocked in headless Chrome).
    driver.execute_script("""
        const _origFetch = window.fetch.bind(window);
        window._capturedShareUrl = null;
        window.fetch = async function(url, opts) {
            const resp = await _origFetch(url, opts);
            if (typeof url === 'string'
                    && url.includes('/api/options/ai-assist/share')
                    && !url.includes('decode')) {
                resp.clone().json().then(function(j) {
                    if (j && j.url) { window._capturedShareUrl = j.url; }
                }).catch(function() {});
            }
            return resp;
        };
    """)

    driver.find_element(By.ID, "ai-share-btn").click()

    # Wait for the status span to flip to a terminal state.
    status = driver.find_element(By.ID, "ai-execute-status")
    WebDriverWait(driver, 8).until(
        lambda d: status.text and (
            "Link copied" in status.text
            or "Share URL ready" in status.text
            or "failed" in status.text.lower()
        )
    )
    _shot(driver, "02_share_status")
    assert "failed" not in status.text.lower(), \
        f"Share button reported failure: {status.text!r}"

    # Extract the captured URL from the JS global we set above.
    share_url = WebDriverWait(driver, 5).until(
        lambda d: d.execute_script("return window._capturedShareUrl;")
    )
    assert share_url, "Share URL was not captured from the fetch intercept"
    assert "aiproposal=" in share_url, \
        f"Expected ?aiproposal= token in share URL, got: {share_url!r}"

    _shot(driver, "03_share_url_captured")

    # Open the share URL in a new tab so the auto-load IIFE runs fresh.
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[-1])
    try:
        driver.get(share_url)

        # The IIFE decodes the token, pre-fills the form, and calls
        # aiAssistPropose() after a 250 ms delay.  Wait for the card.
        WebDriverWait(driver, 40).until(
            lambda d: d.find_element(By.ID, "ai-proposal-card").is_displayed()
        )

        restored_title = driver.find_element(By.ID, "ai-proposal-title").text.strip()
        _shot(driver, "04_restored_proposal")

        assert restored_title and restored_title != "—", \
            "ai-proposal-title is empty after navigating to the share URL"
        assert strategy_name.lower() in restored_title.lower(), (
            f"Strategy '{strategy_name}' not found in restored title '{restored_title}'"
        )
    finally:
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
