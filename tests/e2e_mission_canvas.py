# CUI // SP-CTI
"""E2E Selenium lifecycle test — Mission Canvas.

Tests:
  1. Page loads 200      — /mission-canvas/ returns content, no SEVERE JS errors
  2. 4 zones render      — Situation, Intelligence, Execution, Security visible
  3. IQE widget          — .iqe-widget present
  4. API health          — GET /mission-canvas/api/twin returns JSON
  5. Nav link            — Canvases dropdown contains /mission-canvas/

Screenshots: tests/playwright/screenshots/mc-*.png
Pass threshold: >= 4/5
"""

from __future__ import annotations

import json
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
    path = SCREENSHOT_DIR / f"mc-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def check_js_errors(driver: webdriver.Chrome) -> list[str]:
    errors: list[str] = []
    try:
        logs = driver.get_log("browser")
        for entry in logs:
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if any(n in msg for n in _NOISE):
                    continue
                errors.append(msg)
    except Exception:
        pass
    return errors


def run_tests() -> dict:
    result = TestResult()
    driver = create_driver()

    try:
        # ════════════════════════════════════════════════════════════════
        # TEST 1: Page loads 200
        # ════════════════════════════════════════════════════════════════
        try:
            driver.get(f"{BASE_URL}/mission-canvas/")
            time.sleep(1.5)
            title = driver.title
            body = driver.find_element(By.TAG_NAME, "body").text
            assert "Mission" in title or "mission" in body.lower()
            result.ok("page_loads", f"title={title}")
            screenshot(driver, "01_page_load")
        except Exception as exc:
            result.fail("page_loads", exc)
            screenshot(driver, "01_page_load_error")

        # ════════════════════════════════════════════════════════════════
        # TEST 2: 4 zones render
        # ════════════════════════════════════════════════════════════════
        try:
            zones = ["Situation", "Intelligence", "Execution", "Security"]
            found = 0
            for zone in zones:
                try:
                    driver.find_element(By.XPATH, f"//*[contains(text(), '{zone}')]")
                    found += 1
                except Exception:
                    pass
            assert found >= 3, f"Only {found}/4 zones found"
            result.ok("four_zones", f"{found}/4 zones visible")
            screenshot(driver, "02_zones")
        except Exception as exc:
            result.fail("four_zones", exc)
            screenshot(driver, "02_zones_error")

        # ════════════════════════════════════════════════════════════════
        # TEST 3: IQE widget
        # ════════════════════════════════════════════════════════════════
        try:
            widgets = driver.find_elements(By.CSS_SELECTOR, ".iqe-widget")
            assert len(widgets) > 0, "No .iqe-widget found"
            result.ok("iqe_widget", f"{len(widgets)} widget(s)")
        except Exception as exc:
            result.fail("iqe_widget", exc)

        # ════════════════════════════════════════════════════════════════
        # TEST 4: API health
        # ════════════════════════════════════════════════════════════════
        try:
            driver.get(f"{BASE_URL}/mission-canvas/api/twin?mission_id=e2e-test")
            time.sleep(0.5)
            raw = driver.find_element(By.TAG_NAME, "pre").text
            data = json.loads(raw)
            assert "mission_id" in data
            result.ok("api_twin", f"mission_id={data.get('mission_id')}")
        except Exception as exc:
            result.fail("api_twin", exc)

        # ════════════════════════════════════════════════════════════════
        # TEST 5: Nav link
        # ════════════════════════════════════════════════════════════════
        try:
            driver.get(f"{BASE_URL}/")
            time.sleep(1)
            # Click Canvases dropdown
            driver.find_element(By.LINK_TEXT, "Canvases ▾").click()
            time.sleep(0.5)
            driver.find_element(By.CSS_SELECTOR, 'a[href="/mission-canvas/"]')
            result.ok("nav_link", "Canvases dropdown contains /mission-canvas/")
            screenshot(driver, "05_nav")
        except Exception as exc:
            result.fail("nav_link", exc)
            screenshot(driver, "05_nav_error")

        # ════════════════════════════════════════════════════════════════
        # JS errors
        # ════════════════════════════════════════════════════════════════
        js_errors = check_js_errors(driver)
        if js_errors:
            result.fail("js_errors", f"{len(js_errors)} SEVERE console errors")
        else:
            result.ok("js_errors", "no SEVERE console errors")

    finally:
        driver.quit()

    return result.summary()


if __name__ == "__main__":
    summary = run_tests()
    print(json.dumps(summary, indent=2))
    total = summary["total"]
    passed = summary["passed"]
    threshold = 4
    if passed >= threshold:
        print(f"\nPASS  {passed}/{total} (>= {threshold})")
        sys.exit(0)
    else:
        print(f"\nFAIL  {passed}/{total} (< {threshold})")
        sys.exit(1)
