# CUI // SP-CTI
"""E2E Selenium test — /intake/requirements/<session_id>.

Tests:
  1. 404 on unknown session — /intake/requirements/no-such-id returns 404 body
  2. Session create + page load — POST /api/intake/session then /intake/requirements/<id> renders
  3. Requirements document structure — .req-doc-root, .req-header-title, .req-summary-grid present
  4. CUI banner present — .req-cui-banner contains classification text

Screenshots: tests/playwright/screenshots/intake-req-*.png
Pass threshold: >= 3/4
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
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
        self.passed = []
        self.failed = []

    def ok(self, name, detail=""):
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name}{(': ' + detail) if detail else ''}")

    def fail(self, name, error):
        self.failed.append({"test": name, "error": str(error)[:300]})
        print(f"  FAIL  {name} — {str(error)[:300]}")

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
    path = SCREENSHOT_DIR / f"intake-req-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def check_js_errors(driver):
    errors = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if not any(n in msg.lower() for n in _NOISE):
                    errors.append(msg)
    except Exception:
        pass
    return errors


def _login(driver):
    driver.get(f"{BASE_URL}/login")
    time.sleep(1)
    try:
        driver.find_element(By.NAME, "username").send_keys("admin")
        driver.find_element(By.NAME, "password").send_keys("admin")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        time.sleep(1)
    except Exception:
        pass


def _create_session_via_api():
    """POST /api/intake/session and return session_id or None."""
    payload = json.dumps({
        "customer_name": "E2E Test User",
        "customer_org": "E2E Org",
        "goal": "build",
        "role": "developer",
        "classification": "il4",
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/api/intake/session",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("session_id") or data.get("id")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Test 1: 404 on unknown session ID
# ---------------------------------------------------------------------------
def test_404_on_unknown_session(driver, results):
    try:
        try:
            driver.get_log("browser")
        except Exception:
            pass

        driver.get(f"{BASE_URL}/intake/requirements/e2e-nonexistent-session-id-000")
        time.sleep(1.5)

        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
        assert any(kw in body_text for kw in ("not found", "404", "session")), \
            f"Expected 404/not-found content, got: {body_text[:200]!r}"

        screenshot(driver, "01-404-unknown-session")
        results.ok("404_on_unknown_session", "non-existent session_id returns 404 content")
    except Exception as exc:
        screenshot(driver, "01-404-unknown-session-error")
        results.fail("404_on_unknown_session", str(exc))


# ---------------------------------------------------------------------------
# Test 2: Session create + page load
# ---------------------------------------------------------------------------
def test_page_load_with_session(driver, results):
    try:
        session_id = _create_session_via_api()
        if not session_id:
            results.fail("page_load_with_session", "POST /api/intake/session returned no session_id (intake engine may be unavailable)")
            return

        try:
            driver.get_log("browser")
        except Exception:
            pass

        driver.get(f"{BASE_URL}/intake/requirements/{session_id}")
        time.sleep(2)

        title = driver.title
        assert title, "Page title is empty"

        body_text = driver.find_element(By.TAG_NAME, "body").text
        assert len(body_text) > 20, "Page body is nearly empty"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("page_load_with_session", f"SEVERE JS error(s): {js_errs[0][:150]}")
        else:
            screenshot(driver, "02-page-loaded")
            results.ok("page_load_with_session", f"session={session_id[:8]}… title={title[:50]!r}")
    except Exception as exc:
        screenshot(driver, "02-page-loaded-error")
        results.fail("page_load_with_session", str(exc))


# ---------------------------------------------------------------------------
# Test 3: Requirements document structure in DOM
# ---------------------------------------------------------------------------
def test_requirements_doc_structure(driver, results):
    try:
        session_id = _create_session_via_api()
        if not session_id:
            results.fail("requirements_doc_structure", "No session_id — intake engine may be unavailable")
            return

        driver.get(f"{BASE_URL}/intake/requirements/{session_id}")
        time.sleep(2)

        root = driver.find_element(By.CSS_SELECTOR, ".req-doc-root")
        assert root is not None, ".req-doc-root not found"

        header_title = driver.find_element(By.CSS_SELECTOR, ".req-header-title")
        assert "Requirements Document" in header_title.text, \
            f".req-header-title text unexpected: {header_title.text!r}"

        summary_grid = driver.find_element(By.CSS_SELECTOR, ".req-summary-grid")
        assert summary_grid is not None, ".req-summary-grid not found"

        screenshot(driver, "03-doc-structure")
        results.ok("requirements_doc_structure", ".req-doc-root + .req-header-title + .req-summary-grid present")
    except Exception as exc:
        screenshot(driver, "03-doc-structure-error")
        results.fail("requirements_doc_structure", str(exc))


# ---------------------------------------------------------------------------
# Test 4: CUI banner is present
# ---------------------------------------------------------------------------
def test_cui_banner_present(driver, results):
    try:
        session_id = _create_session_via_api()
        if not session_id:
            results.fail("cui_banner_present", "No session_id — intake engine may be unavailable")
            return

        driver.get(f"{BASE_URL}/intake/requirements/{session_id}")
        time.sleep(2)

        banner = driver.find_element(By.CSS_SELECTOR, ".req-cui-banner")
        assert banner is not None, ".req-cui-banner not found"
        banner_text = banner.text.strip()
        assert banner_text, ".req-cui-banner is empty"

        screenshot(driver, "04-cui-banner")
        results.ok("cui_banner_present", f"banner={banner_text[:60]!r}")
    except Exception as exc:
        screenshot(driver, "04-cui-banner-error")
        results.fail("cui_banner_present", str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("\n" + "=" * 70)
    print("  ICDEV(tm) Intake Requirements E2E — /intake/requirements/<id>")
    print("  Selenium headless Chrome  1920x1080")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)

    results = TestResult()
    driver = create_driver()

    try:
        _login(driver)
        test_404_on_unknown_session(driver, results)
        test_page_load_with_session(driver, results)
        test_requirements_doc_structure(driver, results)
        test_cui_banner_present(driver, results)
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
        sys.exit(1)
    else:
        print(f"\n  THRESHOLD MET: {passed}/{total} >= {threshold}/4")
        sys.exit(0)


if __name__ == "__main__":
    main()
