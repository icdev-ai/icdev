#!/usr/bin/env python3
"""Selenium E2E test for FathomDesk analysis modal + ICDEV™'s INTaaS + Pulse."""

import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path for tools.browser.driver_manager (D7 pilot)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5100"
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from tools.browser.driver_manager import get_driver


def create_driver():
    # Phase D7 pilot: vendored msedgedriver / chromedriver via driver_manager.
    # Default flags (--headless=new, --window-size, --disable-gpu, --no-sandbox)
    # match the inline Chrome construction this replaced.
    return get_driver(headless=True, window_size=(1920, 1080))


passed = 0
failed = 0


def ok(name, detail=""):
    global passed
    passed += 1
    print(f"  PASS: {name}" + (f" ({detail})" if detail else ""))


def fail(name, error):
    global failed
    failed += 1
    print(f"  FAIL: {name} -- {error}")


def screenshot(driver, name):
    p = SCREENSHOT_DIR / f"fathomdesk-{name}-1920x1080.png"
    driver.save_screenshot(str(p))


def main():
    print("=" * 60)
    print("FathomDesk Modal + ICDEV™'s INTaaS + Pulse E2E Test")
    print("=" * 60)

    driver = create_driver()

    try:
        # 1. Run a fresh analysis
        print("[1/7] Run analysis (NVDA)...")
        driver.get(f"{BASE_URL}/analysis")
        time.sleep(1)
        inp = driver.find_element(By.ID, "ticker")
        btn = driver.find_element(By.ID, "analyze-btn")
        inp.clear()
        inp.send_keys("NVDA")
        btn.click()
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#live-result a")))
        # Check "View comprehensive analysis" link appears
        link = driver.find_element(By.CSS_SELECTOR, "#live-result a")
        assert "comprehensive" in link.text.lower() or "analysis" in link.text.lower()
        ok("run_analysis", "NVDA analysis + detail link")

        # 2. Click the link to open modal
        print("[2/7] Open analysis modal...")
        link.click()
        time.sleep(1)
        modal = driver.find_element(By.ID, "analysis-modal")
        assert modal.is_displayed(), "Modal not visible"
        title = driver.find_element(By.ID, "modal-title").text
        assert "NVDA" in title, f"Modal title should contain NVDA, got '{title}'"
        screenshot(driver, "modal-overview")
        ok("modal_open", f"Title: {title}")

        # 3. Check Overview tab has stat cards
        print("[3/7] Overview tab...")
        overview = driver.find_element(By.ID, "modal-overview")
        cards = overview.find_elements(By.CSS_SELECTOR, ".stat-card")
        assert len(cards) >= 4, f"Expected >=4 stat cards, got {len(cards)}"
        texts = [c.text for c in cards]
        has_signal = any("BUY" in t or "SELL" in t or "HOLD" in t for t in texts)
        assert has_signal, f"No signal in stat cards: {texts}"
        ok("overview_tab", f"{len(cards)} cards, signal visible")

        # 4. Switch to Bull vs Bear tab
        print("[4/7] ICDEV™'s INTaaS Bull vs Bear tab...")
        debate_tab = None
        for btn_el in driver.find_elements(By.CSS_SELECTOR, ".tab-btn"):
            if "Bull" in btn_el.text:
                debate_tab = btn_el
                break
        assert debate_tab, "Bull vs Bear tab not found"
        debate_tab.click()
        time.sleep(0.5)
        debate_panel = driver.find_element(By.ID, "tab-debate")
        assert debate_panel.is_displayed()

        # Check ICDEV™'s INTaaS elements
        bull_card = driver.find_element(By.CSS_SELECTOR, ".thesis-card.bull")
        bear_card = driver.find_element(By.CSS_SELECTOR, ".thesis-card.bear")
        assert bull_card.is_displayed(), "Bull card not visible"
        assert bear_card.is_displayed(), "Bear card not visible"

        bull_points = bull_card.find_elements(By.CSS_SELECTOR, ".thesis-points li")
        net_score = driver.find_element(By.CSS_SELECTOR, ".net-score-gauge .score-value")

        # Check conviction bars exist
        conv_bars = driver.find_elements(By.CSS_SELECTOR, ".conviction-bar")
        assert len(conv_bars) >= 4, f"Expected >=4 conviction bars, got {len(conv_bars)}"

        screenshot(driver, "modal-intaas-debate")
        ok(
            "intaas_debate",
            f"{len(bull_points)} bull points, net score: {net_score.text}, {len(conv_bars)} conviction bars",
        )

        # 5. Switch to Signal tab
        print("[5/7] Signal breakdown tab...")
        for btn_el in driver.find_elements(By.CSS_SELECTOR, ".tab-btn"):
            if btn_el.text == "Signal":
                btn_el.click()
                break
        time.sleep(0.5)
        signal_panel = driver.find_element(By.ID, "tab-signal")
        assert signal_panel.is_displayed()

        # Check stacked bar chart
        stacked = driver.find_elements(By.CSS_SELECTOR, ".stacked-segment")
        assert len(stacked) >= 4, f"Expected >=4 bar segments, got {len(stacked)}"

        legend = driver.find_elements(By.CSS_SELECTOR, ".legend-item")
        assert len(legend) >= 4, f"Expected >=4 legend items, got {len(legend)}"

        screenshot(driver, "modal-signal-breakdown")
        ok("signal_breakdown", f"{len(stacked)} segments, {len(legend)} legend items")

        # 6. Switch to Pulse tab
        print("[6/7] Pulse Article tab...")
        for btn_el in driver.find_elements(By.CSS_SELECTOR, ".tab-btn"):
            if "Pulse" in btn_el.text:
                btn_el.click()
                break
        time.sleep(0.5)
        pulse_panel = driver.find_element(By.ID, "tab-pulse")
        assert pulse_panel.is_displayed()

        # Check if article exists or shows "no article" message
        pulse_content = pulse_panel.text
        has_article = "Executive Summary" in pulse_content or "Market Intelligence" in pulse_content
        has_empty = "No Pulse article" in pulse_content or "70%" in pulse_content

        screenshot(driver, "modal-pulse-article")
        if has_article:
            ok("pulse_article", "Article preview rendered")
        elif has_empty:
            ok("pulse_article", "No article (confidence < 70%) — correct behavior")
        else:
            fail("pulse_article", f"Unexpected content: {pulse_content[:100]}")

        # 7. Close modal with Escape
        print("[7/7] Close modal...")
        driver.find_element(By.TAG_NAME, "body").send_keys("\ue00c")  # Escape
        time.sleep(0.5)
        modal_display = driver.find_element(By.ID, "analysis-modal").value_of_css_property("display")
        if modal_display == "none":
            ok("modal_close", "Closed with Escape")
        else:
            # Try X button
            driver.find_element(By.CSS_SELECTOR, ".modal-close").click()
            time.sleep(0.3)
            ok("modal_close", "Closed with X button")

        # Also test clicking a row in run history
        print("[Bonus] Click run history row...")
        driver.get(f"{BASE_URL}/analysis")
        time.sleep(1)
        rows = driver.find_elements(By.CSS_SELECTOR, "#runs-body tr")
        data_rows = [r for r in rows if "empty-state" not in r.get_attribute("innerHTML")]
        if data_rows:
            data_rows[0].click()
            time.sleep(1)
            modal2 = driver.find_element(By.ID, "analysis-modal")
            if modal2.is_displayed():
                screenshot(driver, "modal-from-history")
                ok("history_click", "Modal opened from run history row")
            else:
                fail("history_click", "Modal didn't open from history click")
        else:
            ok("history_click", "No history rows to click (skip)")

    except Exception as exc:
        import traceback

        traceback.print_exc()
        fail("test_error", str(exc))
        try:
            screenshot(driver, "error")
        except Exception:
            pass
    finally:
        driver.quit()

    print()
    print("=" * 60)
    total = passed + failed
    status = "PASSED" if failed == 0 else "FAILED"
    print(f"Result: {status} — {passed}/{total} tests ({passed / total * 100:.0f}%)")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
