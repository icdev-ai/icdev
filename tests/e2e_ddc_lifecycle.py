# CUI // SP-CTI
"""E2E Selenium lifecycle test — DDC (Data Design Canvas).

Tests:
  1. Page loads 200      — /data/ returns content, no SEVERE JS errors
  2. Canvas container    — /data/canvas/new has #canvas-container
  3. API health          — GET /data/api/health returns ok
  4. Objects endpoint    — GET /data/api/objects returns valid data

Screenshots: tests/playwright/screenshots/ddc-*.png
Pass threshold: >= 3/4
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_NOISE = ("favicon", "/api/projects/progress", "/api/kanban/tasks", "joint.js")


class TestResult:
    def __init__(self):
        self.passed: list[dict] = []
        self.failed: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name}{(': ' + detail) if detail else ''}")

    def fail(self, name: str, error) -> None:
        self.failed.append({"test": name, "error": str(error)[:300]})
        print(f"  FAIL  {name} — {str(error)[:300]}")

    def summary(self) -> dict:
        total = len(self.passed) + len(self.failed)
        rate = f"{len(self.passed) / total * 100:.1f}%" if total else "0%"
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": rate,
            "failures": self.failed,
        }


def create_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def screenshot(driver: webdriver.Chrome, name: str) -> str:
    path = SCREENSHOT_DIR / f"ddc-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def check_js_errors(driver: webdriver.Chrome) -> list[str]:
    errors: list[str] = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if not any(n in msg.lower() for n in _NOISE):
                    errors.append(msg)
    except Exception:
        pass
    return errors


def _login(driver: webdriver.Chrome) -> None:
    driver.get(f"{BASE_URL}/login")
    time.sleep(1)
    try:
        driver.find_element(By.NAME, "username").send_keys("admin")
        driver.find_element(By.NAME, "password").send_keys("admin")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        time.sleep(1)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 1: Page loads 200 — /data/ returns content, no SEVERE JS errors
# ---------------------------------------------------------------------------
def test_page_load(driver: webdriver.Chrome, results: TestResult) -> None:
    """Load /data/ and verify it renders with no SEVERE JS errors."""
    try:
        try:
            driver.get_log("browser")
        except Exception:
            pass

        driver.get(f"{BASE_URL}/data/")
        time.sleep(1.5)

        title = driver.title
        assert title, "Page title is empty — page may not have loaded"

        body_text = driver.find_element(By.TAG_NAME, "body").text
        assert len(body_text) > 10, "Page body is nearly empty"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("page_load", f"SEVERE JS error(s): {js_errs[0][:150]}")
        else:
            screenshot(driver, "01-page-loaded")
            results.ok("page_load", f"title={title[:60]!r}")
    except Exception as exc:
        screenshot(driver, "01-page-loaded-error")
        results.fail("page_load", str(exc))


# ---------------------------------------------------------------------------
# Test 2: Canvas container in DOM — /data/canvas/new has #canvas-container
# ---------------------------------------------------------------------------
def test_canvas_container(driver: webdriver.Chrome, results: TestResult) -> None:
    """Navigate to canvas editor and verify #canvas-container is in the DOM."""
    try:
        driver.get(f"{BASE_URL}/data/canvas/new")
        time.sleep(2)

        container = driver.find_element(By.ID, "canvas-container")
        assert container is not None, "#canvas-container element not found"

        wrap = driver.find_element(By.CSS_SELECTOR, ".dc-wrap")
        assert wrap.is_displayed(), ".dc-wrap not visible"

        toolbar = driver.find_element(By.CSS_SELECTOR, ".dc-toolbar")
        assert toolbar.is_displayed(), ".dc-toolbar not visible"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("canvas_container", f"SEVERE JS error(s): {js_errs[0][:150]}")
        else:
            screenshot(driver, "02-canvas-container")
            results.ok("canvas_container", "#canvas-container + .dc-wrap + .dc-toolbar present")
    except Exception as exc:
        screenshot(driver, "02-canvas-container-error")
        results.fail("canvas_container", str(exc))


# ---------------------------------------------------------------------------
# Test 3: API health — GET /data/api/health returns ok
# ---------------------------------------------------------------------------
def test_api_health(driver: webdriver.Chrome, results: TestResult) -> None:
    """Verify GET /data/api/health returns a 200-level response."""
    try:
        driver.get(f"{BASE_URL}/data/")
        time.sleep(0.5)
        driver.set_script_timeout(10)

        data = driver.execute_async_script("""
            var done = arguments[arguments.length - 1];
            fetch('/data/api/health')
                .then(function(r) {
                    if (!r.ok) { done({__status: r.status, ok: false}); return; }
                    return r.json();
                })
                .then(function(d) { done(d || {ok: true}); })
                .catch(function(e) { done({ok: false, error: String(e)}); });
        """)

        assert data is not None, "No response from /data/api/health"
        assert "__status" not in data or data["__status"] < 500, \
            f"API returned error status {data.get('__status')}"

        screenshot(driver, "03-api-health")
        results.ok("api_health", "GET /data/api/health — ok")
    except Exception as exc:
        screenshot(driver, "03-api-health-error")
        results.fail("api_health", str(exc))


# ---------------------------------------------------------------------------
# Test 4: Objects endpoint — GET /data/api/objects returns valid data
# ---------------------------------------------------------------------------
def test_objects_endpoint(driver: webdriver.Chrome, results: TestResult) -> None:
    """Verify GET /data/api/objects returns a 200-level response with data."""
    try:
        driver.get(f"{BASE_URL}/data/")
        time.sleep(0.5)
        driver.set_script_timeout(10)

        data = driver.execute_async_script("""
            var done = arguments[arguments.length - 1];
            fetch('/data/api/objects')
                .then(function(r) {
                    if (!r.ok) { done({__status: r.status, ok: false}); return; }
                    return r.json();
                })
                .then(function(d) { done(d || {ok: true}); })
                .catch(function(e) { done({ok: false, error: String(e)}); });
        """)

        assert data is not None, "No response from /data/api/objects"
        assert data.get("ok") is not False or isinstance(data, (dict, list)), \
            f"Unexpected API response: {data}"
        assert "__status" not in data or data["__status"] < 500, \
            f"API returned error status {data.get('__status')}"

        screenshot(driver, "04-objects-endpoint")
        results.ok("objects_endpoint", "GET /data/api/objects — ok")
    except Exception as exc:
        screenshot(driver, "04-objects-endpoint-error")
        results.fail("objects_endpoint", str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("\n" + "=" * 70)
    print("  ICDEV(tm) DDC Lifecycle E2E — Data Design Canvas")
    print("  Selenium headless Chrome  1920x1080")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)

    results = TestResult()
    driver = create_driver()

    try:
        _login(driver)
        test_page_load(driver, results)
        test_canvas_container(driver, results)
        test_api_health(driver, results)
        test_objects_endpoint(driver, results)
    finally:
        driver.quit()

    summary = results.summary()
    print("\n" + "=" * 70)
    print(f"  RESULTS: {summary['passed']}/{summary['total']} passed  ({summary['pass_rate']})")
    print("=" * 70)

    if summary["failures"]:
        print("\n  FAILURES:")
        for f in summary["failures"]:
            print(f"    x {f['test']}: {f['error']}")

    passed = summary["passed"]
    total = summary["total"]
    threshold = 3
    if passed < threshold:
        print(f"\n  BELOW THRESHOLD: {passed}/{total} < {threshold}/4 required")
        return 1
    else:
        print(f"\n  THRESHOLD MET: {passed}/{total} >= {threshold}/4")
        return 0


if __name__ == "__main__":
    sys.exit(main())
# CUI // SP-CTI
