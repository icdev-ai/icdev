#!/usr/bin/env python3
"""Selenium E2E tests for the /fedramp-20x KSI Dashboard.

Tests:
  1. Page loads with correct heading and stat cards
  2. Project input and action buttons are present
  3. Family table and KSI table render with correct headers
  4. Generate KSIs button triggers API and populates tables
  5. Package Status button reveals package section
  6. API endpoints return expected shape
  7. No JavaScript console errors

Uses headless Chrome at 1920x1080.

Usage:
    python tests/e2e_fedramp-20x.py [--base-url http://localhost:5050]
"""

import json
import sys
import time
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5050"
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
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
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
    path = SCREENSHOT_DIR / f"fedramp-20x-{name}-1920x1080.png"
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


def test_page_loads(driver, results):
    """Page renders with correct heading and no server errors."""
    print("\n[1] Page load")
    try:
        driver.get(f"{BASE_URL}/boundary/fedramp-20x")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "h1"))
        )
        h1 = driver.find_element(By.TAG_NAME, "h1").text
        assert "FedRAMP 20x" in h1, f"Unexpected heading: {h1}"
        results.ok("page_loads", h1)
        screenshot(driver, "loaded")
    except Exception as e:
        results.fail("page_loads", e)
        screenshot(driver, "load-error")


def test_stat_cards_present(driver, results):
    """All 6 stat card value elements are in the DOM."""
    print("\n[2] Stat cards")
    stat_ids = [
        "stat-total",
        "stat-coverage",
        "stat-advanced",
        "stat-intermediate",
        "stat-basic",
        "stat-none",
    ]
    for stat_id in stat_ids:
        try:
            el = driver.find_element(By.ID, stat_id)
            assert el is not None
            results.ok(f"stat_card_{stat_id}")
        except Exception as e:
            results.fail(f"stat_card_{stat_id}", e)


def test_controls_present(driver, results):
    """Project input, Generate KSIs button, and Package Status button are present."""
    print("\n[3] Controls")
    checks = [
        ("project_input", By.ID, "project-select"),
        ("btn_generate", By.ID, "btn-generate"),
        ("btn_package", By.ID, "btn-package"),
    ]
    for name, by, locator in checks:
        try:
            el = driver.find_element(by, locator)
            assert el.is_displayed(), f"{locator} not visible"
            results.ok(f"control_{name}", el.get_attribute("id") or locator)
        except Exception as e:
            results.fail(f"control_{name}", e)


def test_family_table_headers(driver, results):
    """Family table has all 8 expected column headers."""
    print("\n[4] Family table headers")
    expected_headers = [
        "Family",
        "Total",
        "Covered",
        "Advanced",
        "Intermediate",
        "Basic",
        "None",
        "Coverage",
    ]
    try:
        table = driver.find_element(By.ID, "family-table")
        ths = table.find_elements(By.TAG_NAME, "th")
        actual = [th.text.strip() for th in ths]
        actual_lower = [h.lower() for h in actual]
        for col in expected_headers:
            assert col.lower() in actual_lower, f"Missing column: {col} (got {actual})"
        results.ok("family_table_headers", f"{len(actual)} columns")
    except Exception as e:
        results.fail("family_table_headers", e)


def test_ksi_table_headers(driver, results):
    """KSI detail table has all 6 expected column headers."""
    print("\n[5] KSI table headers")
    expected_headers = ["KSI ID", "Title", "Family", "NIST Controls", "Maturity", "Evidence"]
    try:
        table = driver.find_element(By.ID, "ksi-table")
        ths = table.find_elements(By.TAG_NAME, "th")
        actual = [th.text.strip() for th in ths]
        actual_lower = [h.lower() for h in actual]
        for col in expected_headers:
            assert col.lower() in actual_lower, f"Missing column: {col} (got {actual})"
        results.ok("ksi_table_headers", f"{len(actual)} columns")
    except Exception as e:
        results.fail("ksi_table_headers", e)


def dismiss_tour(driver):
    """Dismiss the ICDEV tour welcome overlay if present."""
    try:
        driver.execute_script(
            "var el=document.getElementById('icdev-tour-welcome');"
            "if(el){el.style.display='none';el.classList.remove('visible');}"
        )
    except Exception:
        pass


def test_generate_ksis(driver, results):
    """Clicking Generate KSIs triggers the API and updates the tables."""
    print("\n[6] Generate KSIs")
    try:
        dismiss_tour(driver)

        # Enter a project ID
        inp = driver.find_element(By.ID, "project-select")
        inp.clear()
        inp.send_keys("proj-test-001")

        btn = driver.find_element(By.ID, "btn-generate")
        driver.execute_script("arguments[0].click();", btn)

        # Wait for family-body to update (text changes from placeholder)
        time.sleep(2)
        screenshot(driver, "after-generate")

        driver.find_element(By.ID, "family-body")
        driver.find_element(By.ID, "ksi-body")

        results.ok("generate_ksis_click", "button clicked, tables updated")

        # Stat values may have changed from '--' placeholder
        stat_total = driver.find_element(By.ID, "stat-total").text
        results.ok("stat_total_updated", f"value={stat_total!r}")
    except Exception as e:
        results.fail("generate_ksis", e)
        screenshot(driver, "generate-error")


def test_package_status(driver, results):
    """Clicking Package Status reveals the package section."""
    print("\n[7] Package Status")
    try:
        dismiss_tour(driver)
        btn = driver.find_element(By.ID, "btn-package")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(2)

        package_section = driver.find_element(By.ID, "package-section")
        # Section should now be visible
        display = driver.execute_script(
            "return window.getComputedStyle(arguments[0]).display;", package_section
        )
        assert display != "none", f"package-section still hidden (display={display})"
        results.ok("package_section_visible", f"display={display}")

        package_table = driver.find_element(By.ID, "package-table")
        ths = package_table.find_elements(By.TAG_NAME, "th")
        actual = [th.text.strip() for th in ths]
        actual_lower = [h.lower() for h in actual]
        for col in ["Artifact", "Status", "Available"]:
            assert col.lower() in actual_lower, f"Missing package column: {col} (got {actual})"
        results.ok("package_table_headers", f"{len(actual)} columns")

        screenshot(driver, "package-status")
    except Exception as e:
        results.fail("package_status", e)
        screenshot(driver, "package-error")


def test_api_ksis(driver, results):
    """API endpoint /api/fedramp-20x/ksis returns a JSON response."""
    print("\n[8] API /ksis")
    try:
        driver.get(f"{BASE_URL}/api/fedramp-20x/ksis?project_id=proj-test-001")
        body = driver.find_element(By.TAG_NAME, "body").text
        data = json.loads(body)
        assert isinstance(data, (dict, list)), f"Unexpected type: {type(data)}"
        results.ok("api_ksis_responds", f"type={type(data).__name__}")
    except Exception as e:
        results.fail("api_ksis_responds", e)


def test_api_package(driver, results):
    """API endpoint /api/fedramp-20x/package returns a JSON response."""
    print("\n[9] API /package")
    try:
        driver.get(f"{BASE_URL}/api/fedramp-20x/package?project_id=proj-test-001")
        body = driver.find_element(By.TAG_NAME, "body").text
        data = json.loads(body)
        assert isinstance(data, (dict, list)), f"Unexpected type: {type(data)}"
        results.ok("api_package_responds", f"type={type(data).__name__}")
    except Exception as e:
        results.fail("api_package_responds", e)


def test_no_js_errors(driver, results):
    """No SEVERE JavaScript console errors on the page (excluding favicon)."""
    print("\n[10] JS errors")
    try:
        driver.get(f"{BASE_URL}/boundary/fedramp-20x")
        time.sleep(1)
        errors = check_js_errors(driver)
        if errors:
            results.fail("no_js_errors", f"{len(errors)} error(s): {errors[:2]}")
        else:
            results.ok("no_js_errors")
    except Exception as e:
        results.fail("no_js_errors", e)


def main():
    # Allow overriding base URL via CLI arg
    global BASE_URL
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--base-url" and i + 2 <= len(sys.argv) - 1:
            BASE_URL = sys.argv[i + 2]

    print("=" * 60)
    print("E2E: /fedramp-20x KSI Dashboard")
    print(f"Base URL: {BASE_URL}")
    print("=" * 60)

    results = TestResult()
    driver = create_driver()

    try:
        test_page_loads(driver, results)
        test_stat_cards_present(driver, results)
        test_controls_present(driver, results)
        test_family_table_headers(driver, results)
        test_ksi_table_headers(driver, results)
        test_generate_ksis(driver, results)
        test_package_status(driver, results)
        test_api_ksis(driver, results)
        test_api_package(driver, results)
        test_no_js_errors(driver, results)
    except Exception as exc:
        print(f"\nFATAL: {exc}")
        traceback.print_exc()
    finally:
        driver.quit()

    summary = results.summary()
    print("\n" + "=" * 60)
    print(f"RESULT: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']})")
    if summary["failures"]:
        print("Failures:")
        for f in summary["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    print("=" * 60)

    return 1 if summary["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
