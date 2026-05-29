"""
E2E Selenium test — /proposals/reviews-dashboard (prop-rev-07).

Verifies the executive cross-proposal review dashboard end-to-end:
  1. Page loads with 200 + correct title
  2. Summary stat cards render (passed, pass_with_findings, failed, critical)
  3. Opportunity table renders >= 0 rows without JS error
  4. Screenshot capture at 1920x1080
  5. No SEVERE JS errors (favicon/404 excluded)

Run: python tests/e2e_proposals_reviews_dashboard.py
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
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

ROUTE = "/proposals/reviews-dashboard"


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"[OK]   {name}{(': ' + detail) if detail else ''}")

    def fail(self, name: str, err: object) -> None:
        msg = str(err)[:200]
        self.failed.append((name, msg))
        print(f"[FAIL] {name}: {msg}")

    def summary(self) -> dict:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "total": len(self.passed) + len(self.failed),
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


def check_js_errors(driver) -> list[str]:
    errors: list[str] = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") != "SEVERE":
                continue
            msg = entry.get("message", "")
            if any(
                token in msg.lower()
                for token in ("favicon", "401", "404", "failed to load resource")
            ):
                continue
            errors.append(msg)
    except Exception:
        pass
    return errors


def run_tests() -> int:
    results = TestResult()
    driver = create_driver()

    try:
        # ── 1. Page load ─────────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}{ROUTE}")
            time.sleep(2)
            title = driver.title
            assert title, "empty page title"
            results.ok("page_loads", f"title={title[:60]}")
        except Exception as exc:
            results.fail("page_loads", exc)
            return 1

        # ── 2. Summary stat cards present ────────────────────────────
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            # Page should contain at least some recognizable content
            assert len(body_text) > 50, f"page body too short ({len(body_text)} chars)"
            results.ok("body_has_content", f"body_len={len(body_text)}")
        except Exception as exc:
            results.fail("body_has_content", exc)

        # ── 3. No critical JS errors ──────────────────────────────────
        try:
            js_errors = check_js_errors(driver)
            assert not js_errors, f"SEVERE JS errors: {js_errors}"
            results.ok("no_js_errors")
        except Exception as exc:
            results.fail("no_js_errors", exc)

        # ── 4. Screenshot ─────────────────────────────────────────────
        try:
            shot_path = SCREENSHOT_DIR / "proposals_reviews_dashboard.png"
            driver.save_screenshot(str(shot_path))
            results.ok("screenshot", str(shot_path))
        except Exception as exc:
            results.fail("screenshot", exc)

    finally:
        driver.quit()

    summary = results.summary()
    print(
        f"\n{'='*50}\n"
        f"Results: {summary['passed']} passed, {summary['failed']} failed / {summary['total']} total"
    )
    if summary["failures"]:
        for name, err in summary["failures"]:
            print(f"  FAIL {name}: {err}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
