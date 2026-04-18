# CUI // SP-CTI
"""E2E Selenium test — IQE dashboard (/network/iqe).

Tests:
  1. Page load — layout, query list sidebar, main panel visible
  2. Query list populated — at least 1 .iqe query item in sidebar
  3. Seed NDC fixture — POST /network/api/iqe/seed inserts test devices
  4. Run first query — click first item, wait for result rows to appear
  5. Assert results + screenshot — ≥1 result row, screenshot saved, no SEVERE JS errors

Starts a self-contained NDC Flask server on TEST_PORT if /network/iqe is not
reachable on BASE_URL (e.g. main server has NDC disabled).
"""

from __future__ import annotations

import os
import sys
import time
import threading
import urllib.request
from pathlib import Path

# Ensure this worktree's tools package takes precedence over any installed copy.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
TEST_PORT = int(os.environ.get("IQE_TEST_PORT", "5051"))
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_test_server = None  # wsgiref.simple_server.WSGIServer instance when started


# ---------------------------------------------------------------------------
# Self-contained NDC test server
# ---------------------------------------------------------------------------

def _ndc_reachable(base_url: str) -> bool:
    """Return True if /network/iqe responds with 200 on base_url."""
    try:
        with urllib.request.urlopen(f"{base_url}/network/iqe", timeout=3) as r:  # nosec B310
            return r.status == 200
    except Exception:
        return False


def _start_ndc_server(port: int):
    """Launch a minimal Flask app with the NDC blueprint on localhost:port."""
    os.environ.setdefault("ICDEV_NETWORK_ENABLED", "true")

    from flask import Flask
    from wsgiref.simple_server import make_server

    _root = Path(__file__).resolve().parent.parent
    flask_app = Flask(
        __name__,
        template_folder=str(_root / "tools" / "dashboard" / "templates"),
        static_folder=str(_root / "tools" / "dashboard" / "static"),
    )
    flask_app.secret_key = "ndc-e2e-test"

    from tools.network.blueprint import create_network_blueprint
    bp = create_network_blueprint()
    flask_app.register_blueprint(bp, url_prefix="/network")

    srv = make_server("127.0.0.1", port, flask_app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # Wait for server to accept connections
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/network/iqe", timeout=1)  # nosec B310
            break
        except Exception:
            time.sleep(0.25)
    return srv


def _ensure_server() -> str:
    """Return the base URL to use, starting a test server if needed."""
    global _test_server
    if _ndc_reachable(BASE_URL):
        return BASE_URL
    print(f"  INFO  {BASE_URL}/network/iqe not reachable — starting local NDC test server on :{TEST_PORT}")
    _test_server = _start_ndc_server(TEST_PORT)
    return f"http://127.0.0.1:{TEST_PORT}"


# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name, detail=""):
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name} {detail}")

    def fail(self, name, error):
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL  {name} — {error}")

    def summary(self):
        total = len(self.passed) + len(self.failed)
        rate = f"{len(self.passed) / total * 100:.1f}%" if total else "0%"
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": rate,
            "failures": self.failed,
        }


def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:  # nosec B110
        pass
    return str(path)


def check_js_errors(driver):
    errors = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if "favicon" not in msg.lower():
                    errors.append(msg)
    except Exception:  # nosec B110
        pass
    return errors


# ---------------------------------------------------------------------------
# Step 1: Page load — verify layout
# ---------------------------------------------------------------------------
def test_page_load(driver, results, base_url):
    """Load /network/iqe and verify sidebar + main panel are rendered."""
    try:
        driver.get(f"{base_url}/network/iqe")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "iqe-query-list"))
        )

        sidebar = driver.find_element(By.ID, "iqe-query-list")
        if not sidebar.is_displayed():
            raise AssertionError("#iqe-query-list not visible")  # nosec B101

        source_panel = driver.find_element(By.ID, "iqe-source")
        if not source_panel.is_displayed():
            raise AssertionError("#iqe-source not visible")  # nosec B101

        run_btn = driver.find_element(By.ID, "iqe-run-btn")
        if not run_btn.is_displayed():
            raise AssertionError("#iqe-run-btn not visible")  # nosec B101

        results_panel = driver.find_element(By.ID, "iqe-results")
        if not results_panel.is_displayed():
            raise AssertionError("#iqe-results not visible")  # nosec B101

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("page_load_js_errors", "; ".join(js_errs[:3]))
        else:
            results.ok("page_load", "sidebar + source panel + results panel rendered, no JS errors")
    except Exception as exc:
        screenshot(driver, "iqe-step1-error")
        results.fail("page_load", str(exc))


# ---------------------------------------------------------------------------
# Step 2: Query list populated — at least 1 query item
# ---------------------------------------------------------------------------
def test_query_list_populated(driver, results, base_url):
    """Verify the sidebar has at least 1 .iqe query item."""
    try:
        driver.get(f"{base_url}/network/iqe")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".iqe-query-item"))
        )

        items = driver.find_elements(By.CSS_SELECTOR, ".iqe-query-item")
        if len(items) < 1:
            raise AssertionError(f"Expected ≥1 query items, got {len(items)}")  # nosec B101

        names = [i.find_element(By.CSS_SELECTOR, ".qname").text for i in items]
        results.ok("query_list_populated", f"{len(items)} queries: {', '.join(names[:3])}")
    except Exception as exc:
        screenshot(driver, "iqe-step2-error")
        results.fail("query_list_populated", str(exc))


# ---------------------------------------------------------------------------
# Step 3: Seed NDC fixture
# ---------------------------------------------------------------------------
def test_seed_fixture(driver, results, base_url):
    """POST /network/api/iqe/seed and verify test devices are inserted."""
    try:
        driver.get(f"{base_url}/network/iqe")
        time.sleep(0.5)
        driver.set_script_timeout(15)

        data = driver.execute_async_script("""
            var done = arguments[arguments.length - 1];
            fetch('/network/api/iqe/seed', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: '{}'
            })
            .then(function(r) { return r.json(); })
            .then(function(d) { done(d); })
            .catch(function(e) { done({ok: false, error: e.toString()}); });
        """)

        if data.get("ok") is not True:
            raise AssertionError(f"Seed returned ok=false: {data}")  # nosec B101
        seeded = data.get("seeded", 0)
        results.ok("seed_fixture", f"seeded={seeded} devices into ni_devices")
    except Exception as exc:
        screenshot(driver, "iqe-step3-error")
        results.fail("seed_fixture", str(exc))


# ---------------------------------------------------------------------------
# Step 4: Click first query and run it
# ---------------------------------------------------------------------------
def test_run_first_query(driver, results, base_url):
    """Click the first query item, then click Run, and wait for results to populate."""
    try:
        driver.get(f"{base_url}/network/iqe")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".iqe-query-item"))
        )

        first_item = driver.find_element(By.CSS_SELECTOR, ".iqe-query-item")
        filename = first_item.get_attribute("data-filename")
        first_item.click()

        # Wait for source panel to load the query body
        WebDriverWait(driver, 8).until(
            lambda d: "foreach" in d.find_element(By.ID, "iqe-source").text.lower()
        )

        # Click Run
        run_btn = driver.find_element(By.ID, "iqe-run-btn")
        if run_btn.get_attribute("disabled"):
            raise AssertionError("#iqe-run-btn still disabled after query selected")  # nosec B101
        run_btn.click()

        # Wait for status update
        WebDriverWait(driver, 12).until(
            lambda d: d.find_element(By.ID, "iqe-run-status").text.strip() != ""
            and "Running" not in d.find_element(By.ID, "iqe-run-status").text
        )

        status_text = driver.find_element(By.ID, "iqe-run-status").text
        if "Error" in status_text:
            raise AssertionError(f"Run returned error: {status_text}")  # nosec B101

        results.ok("run_first_query", f"filename={filename}; status='{status_text}'")
    except Exception as exc:
        screenshot(driver, "iqe-step4-error")
        results.fail("run_first_query", str(exc))


# ---------------------------------------------------------------------------
# Step 5: Assert ≥1 result row, screenshot, no SEVERE JS errors
# ---------------------------------------------------------------------------
def test_result_rows_and_screenshot(driver, results, base_url):
    """Verify at least 1 result row in the DOM, take screenshot, check JS errors."""
    try:
        driver.get(f"{base_url}/network/iqe")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".iqe-query-item"))
        )

        # Select first query
        first_item = driver.find_element(By.CSS_SELECTOR, ".iqe-query-item")
        first_item.click()
        WebDriverWait(driver, 8).until(
            lambda d: "foreach" in d.find_element(By.ID, "iqe-source").text.lower()
        )

        # Run query
        driver.find_element(By.ID, "iqe-run-btn").click()

        # Wait for result table rows
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#iqe-results-tbody tr"))
        )

        rows = driver.find_elements(By.CSS_SELECTOR, "#iqe-results-tbody tr")
        if len(rows) < 1:
            raise AssertionError(f"Expected ≥1 result rows, got {len(rows)}")  # nosec B101

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("result_rows_js_errors", "; ".join(js_errs[:3]))
            screenshot(driver, "iqe-page")
            return

        path = screenshot(driver, "iqe-page")
        results.ok("result_rows_and_screenshot", f"{len(rows)} row(s) in DOM; screenshot → {path}")
    except Exception as exc:
        screenshot(driver, "iqe-page")
        results.fail("result_rows_and_screenshot", str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("\n" + "=" * 70)
    print("  ICDEV™ IQE Dashboard E2E — /network/iqe")
    print("  Selenium headless Chrome  1920×1080")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)

    base_url = _ensure_server()
    if base_url != BASE_URL:
        print(f"  INFO  Using local test server: {base_url}")

    results = TestResult()
    driver = create_driver()

    try:
        test_page_load(driver, results, base_url)
        test_query_list_populated(driver, results, base_url)
        test_seed_fixture(driver, results, base_url)
        test_run_first_query(driver, results, base_url)
        test_result_rows_and_screenshot(driver, results, base_url)
    finally:
        driver.quit()
        if _test_server is not None:
            _test_server.shutdown()

    summary = results.summary()
    print("\n" + "=" * 70)
    print(f"  RESULTS: {summary['passed']}/{summary['total']} passed  ({summary['pass_rate']})")
    print("=" * 70)

    if summary["failures"]:
        print("\n  FAILURES:")
        for f in summary["failures"]:
            print(f"    x {f['test']}: {f['error']}")
        sys.exit(1)
    else:
        print("\n  All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
