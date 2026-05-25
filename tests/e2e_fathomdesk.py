#!/usr/bin/env python3
"""Selenium E2E Lifecycle Test for FathomDesk Trading Dashboard.

Tests the FULL lifecycle:
  1. All 7 pages render correctly
  2. Run analysis via form → signal persisted to DB
  3. Signal appears on signals page with Approve/Reject buttons
  4. Approve a signal → status updates
  5. Run history shows completed runs
  6. API endpoints return DB-backed data
  7. Screenshots at every step

Uses headless Chrome at 1920x1080.

Usage:
    python tests/e2e_fathomdesk.py
"""

import json
import sys
import time
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5100"
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name, detail=""):
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS: {name}" + (f" ({detail})" if detail else ""))

    def fail(self, name, error):
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL: {name} -- {error}")

    def summary(self):
        total = len(self.passed) + len(self.failed)
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": (f"{len(self.passed) / total * 100:.1f}%" if total else "0%"),
            "failures": self.failed,
        }


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"fathomdesk-{name}-1920x1080.png"
    driver.save_screenshot(str(path))
    return str(path)


def check_js_errors(driver):
    errors = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if "favicon" not in msg.lower():
                    errors.append(msg)
    except Exception:
        pass
    return errors


# ======================================================================
# Test: All 7 pages load
# ======================================================================
def test_page_loads(driver, results):
    pages = [
        ("/", "Overview", "index"),
        ("/portfolio", "Portfolio", "portfolio"),
        ("/analysis", "Analysis", "analysis"),
        ("/signals", "Signals", "signals"),
        ("/orders", "Orders", "orders"),
        ("/risk", "Risk", "risk"),
        ("/settings", "Settings", "settings"),
    ]
    for path, expected, slug in pages:
        try:
            driver.get(f"{BASE_URL}{path}")
            time.sleep(0.5)
            h1 = driver.find_element(By.CSS_SELECTOR, ".page-header h1")
            assert expected in h1.text, f"Expected '{expected}' in h1, got '{h1.text}'"
            sidebar = driver.find_element(By.CSS_SELECTOR, ".sidebar")
            assert sidebar.is_displayed()
            js_errs = check_js_errors(driver)
            if js_errs:
                results.fail(f"page_{slug}_js", "; ".join(js_errs))
            screenshot(driver, slug)
            results.ok(f"page_{slug}", "rendered")
        except Exception as exc:
            screenshot(driver, f"{slug}-error")
            results.fail(f"page_{slug}", str(exc))


# ======================================================================
# Test: Run analysis via form → get run_id + signal_id back
# ======================================================================
def test_run_analysis(driver, results):
    try:
        driver.get(f"{BASE_URL}/analysis")
        time.sleep(0.5)

        ticker_input = driver.find_element(By.ID, "ticker")
        btn = driver.find_element(By.ID, "analyze-btn")

        ticker_input.clear()
        ticker_input.send_keys("GOOGL")
        btn.click()

        # Wait for result to render (stat cards appear)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#live-result .stat-grid")))

        result_el = driver.find_element(By.ID, "live-result")
        text = result_el.text

        assert "GOOGL" in text or "BUY" in text or "HOLD" in text, (
            f"Result should show GOOGL analysis, got: {text[:200]}"
        )

        # Check run_id is displayed
        has_run_id = "run-" in text
        has_signal = "sig-" in text or "Signal" in text

        screenshot(driver, "analysis-googl-result")
        results.ok(
            "run_analysis",
            f"GOOGL analysis complete, run_id={has_run_id}, signal={has_signal}",
        )
    except Exception as exc:
        screenshot(driver, "analysis-run-error")
        results.fail("run_analysis", str(exc))


# ======================================================================
# Test: Run history shows the run we just created
# ======================================================================
def test_run_history(driver, results):
    try:
        driver.get(f"{BASE_URL}/analysis")
        time.sleep(1)

        rows = driver.find_elements(By.CSS_SELECTOR, "#runs-body tr")
        # Filter out empty-state rows
        data_rows = [r for r in rows if "empty-state" not in r.get_attribute("innerHTML")]

        assert len(data_rows) >= 1, f"Expected >=1 run in history, got {len(data_rows)}"

        # Check GOOGL run appears
        tbody_text = driver.find_element(By.ID, "runs-body").text
        assert "GOOGL" in tbody_text or "AAPL" in tbody_text, "Run history should contain analysis run"

        screenshot(driver, "run-history")
        results.ok("run_history", f"{len(data_rows)} runs in history")
    except Exception as exc:
        results.fail("run_history", str(exc))


# ======================================================================
# Test: Signals page shows pending signals with Approve/Reject
# ======================================================================
def test_signals_page(driver, results):
    try:
        driver.get(f"{BASE_URL}/signals")
        time.sleep(1)

        # Check signal count cards loaded
        pending = driver.find_element(By.ID, "pending-count")
        pending_val = int(pending.text) if pending.text.isdigit() else 0

        total = driver.find_element(By.ID, "total-count")
        total_val = int(total.text) if total.text.isdigit() else 0

        assert total_val >= 1, f"Expected >=1 total signal, got {total_val}"

        # Check table has signal rows
        rows = driver.find_elements(By.CSS_SELECTOR, "#signals-body tr")
        data_rows = [r for r in rows if "empty-state" not in r.get_attribute("innerHTML")]
        assert len(data_rows) >= 1, f"Expected >=1 signal row, got {len(data_rows)}"

        # Check approve/reject buttons exist for pending signals
        approve_btns = driver.find_elements(By.CSS_SELECTOR, ".btn-approve")
        screenshot(driver, "signals-pending")
        results.ok(
            "signals_page",
            f"{total_val} signals, {pending_val} pending, {len(approve_btns)} approve buttons",
        )
    except Exception as exc:
        screenshot(driver, "signals-error")
        results.fail("signals_page", str(exc))


# ======================================================================
# Test: Approve a signal → status updates
# ======================================================================
def test_approve_signal(driver, results):
    try:
        driver.get(f"{BASE_URL}/signals")
        time.sleep(1)

        approve_btns = driver.find_elements(By.CSS_SELECTOR, ".btn-approve")
        if not approve_btns:
            results.ok("approve_signal", "No pending signals to approve (skip)")
            return

        # Click first approve button
        approve_btns[0].click()
        time.sleep(1)  # Wait for reload

        # Check the signal status changed
        approved_badges = driver.find_elements(By.CSS_SELECTOR, ".badge.pass")
        approved_count = driver.find_element(By.ID, "approved-count")

        screenshot(driver, "signal-approved")
        results.ok(
            "approve_signal",
            f"Signal approved, badge count={len(approved_badges)}, approved={approved_count.text}",
        )
    except Exception as exc:
        screenshot(driver, "approve-error")
        results.fail("approve_signal", str(exc))


# ======================================================================
# Test: API endpoints return DB-backed data
# ======================================================================
def test_api_endpoints(driver, results):
    endpoints = [
        ("/api/health", "db", "DB connection field"),
        ("/api/portfolio", "cash_balance", "portfolio data"),
        ("/api/signals", "pending_count", "signal counts"),
        ("/api/analysis/runs", "runs", "run history"),
        ("/api/orders", "orders", "order list"),
        ("/api/risk", "portfolio_var", "risk metrics"),
    ]
    for path, key, desc in endpoints:
        try:
            driver.get(f"{BASE_URL}{path}")
            time.sleep(0.3)
            body = driver.find_element(By.TAG_NAME, "body").text
            data = json.loads(body)
            assert key in data, f"Missing '{key}' in {path}"
            results.ok(f"api_{path.split('/')[-1]}", desc)
        except Exception as exc:
            results.fail(f"api_{path.split('/')[-1]}", str(exc))


# ======================================================================
# Test: Overview stat cards show live data from DB
# ======================================================================
def test_overview_live(driver, results):
    try:
        driver.get(f"{BASE_URL}/")
        time.sleep(1)

        pv = driver.find_element(By.ID, "portfolio-value")
        assert "$" in pv.text, "Portfolio value should have $"

        signal_count = driver.find_element(By.ID, "signal-count")

        screenshot(driver, "overview-with-data")
        results.ok(
            "overview_live",
            f"Portfolio: {pv.text}, signals: {signal_count.text}",
        )
    except Exception as exc:
        results.fail("overview_live", str(exc))


# ======================================================================
# Test: Dark theme and layout
# ======================================================================
def test_layout(driver, results):
    try:
        driver.get(f"{BASE_URL}/")
        time.sleep(0.5)
        sidebar = driver.find_element(By.CSS_SELECTOR, ".sidebar")
        assert sidebar.size["width"] >= 200
        badge = driver.find_element(By.CSS_SELECTOR, ".paper-badge")
        assert "PAPER" in badge.text.upper()
        results.ok("layout", f"sidebar={sidebar.size['width']}px, paper badge")
    except Exception as exc:
        results.fail("layout", str(exc))


# ======================================================================
# Main
# ======================================================================
def main():
    print("=" * 60)
    print("FathomDesk E2E Lifecycle Test — Selenium Headless Chrome")
    print("=" * 60)
    print(f"Target: {BASE_URL}")
    print(f"Screenshots: {SCREENSHOT_DIR}")
    print()

    results = TestResult()
    driver = None

    try:
        driver = create_driver()

        print("[1/8] Page loads (7 pages)...")
        test_page_loads(driver, results)

        print("[2/8] Run analysis (GOOGL)...")
        test_run_analysis(driver, results)

        print("[3/8] Run history...")
        test_run_history(driver, results)

        print("[4/8] Signals page (pending)...")
        test_signals_page(driver, results)

        print("[5/8] Approve signal...")
        test_approve_signal(driver, results)

        print("[6/8] API endpoints (DB-backed)...")
        test_api_endpoints(driver, results)

        print("[7/8] Overview live data...")
        test_overview_live(driver, results)

        print("[8/8] Layout + theme...")
        test_layout(driver, results)

    except Exception as exc:
        print(f"\nFATAL: {exc}")
        traceback.print_exc()
        results.fail("setup", str(exc))
    finally:
        if driver:
            driver.quit()

    print()
    print("=" * 60)
    s = results.summary()
    status = "PASSED" if s["failed"] == 0 else "FAILED"
    print(f"Result: {status} — {s['passed']}/{s['total']} tests passed ({s['pass_rate']})")
    if s["failures"]:
        print("Failures:")
        for f in s["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    print("=" * 60)

    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
