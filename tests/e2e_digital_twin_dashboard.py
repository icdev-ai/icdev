"""E2E Selenium test — /digital-twin roadmap page.

Asserts:
  1. page loads with HTTP 200 and renders the title
  2. all 8 epic rows render with progress bars
  3. briefs section shows both inspiration + market-scan links
  4. failure-triage stats panel renders
  5. screenshot captured at 1920x1080
  6. no SEVERE JS errors (favicon/404 excluded)

Run: python tests/e2e_digital_twin_dashboard.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

EXPECTED_EPICS = [
    "IQE v0.1",
    "BDC cATO Twin",
    "IDC IaC Generation",
    "IDC IaC Twin",
    "PDC Pipeline Twin",
    "SDC Attack Path Twin",
    "ODC MITRE Coverage Twin",
    "DDC Lineage Adapter",
]


def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=opts)


def main() -> int:
    passed: list = []
    failed: list = []

    def ok(name: str, detail: str = ""):
        passed.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"[OK]   {name}{(': ' + detail) if detail else ''}")

    def fail(name: str, err: object):
        msg = str(err)[:240]
        failed.append((name, msg))
        print(f"[FAIL] {name}: {msg}")

    driver = make_driver()
    try:
        driver.get(f"{BASE_URL}/digital-twin")
        body = driver.page_source

        # 1. Title present
        try:
            assert "Digital Twin Roadmap" in body
            ok("title rendered")
        except Exception as e:
            fail("title rendered", e)

        # 2. All 8 epic titles present
        for ep in EXPECTED_EPICS:
            try:
                assert ep in body
                ok("epic rendered", ep)
            except Exception as e:
                fail(f"epic rendered: {ep}", e)

        # 3. Progress bars present (one per epic)
        try:
            bars = driver.find_elements(By.CSS_SELECTOR, ".dt-bar")
            assert len(bars) == len(EXPECTED_EPICS), f"expected {len(EXPECTED_EPICS)} bars, got {len(bars)}"
            ok("progress bars", f"{len(bars)} rendered")
        except Exception as e:
            fail("progress bars", e)

        # 4. Briefs section
        for label in ("digital-twin-inspiration-brief.md",
                      "digital-twin-market-canvas-implementation-plan.md"):
            try:
                assert label in body
                ok("brief link", label)
            except Exception as e:
                fail(f"brief link: {label}", e)

        # 5. Failure-triage stats present (4 stat cards)
        try:
            stats = driver.find_elements(By.CSS_SELECTOR, ".dt-stat")
            assert len(stats) == 4, f"expected 4 stat cards, got {len(stats)}"
            ok("triage stats", f"{len(stats)} cards")
        except Exception as e:
            fail("triage stats", e)

        # 6. Screenshot
        try:
            shot = SCREENSHOT_DIR / "digital-twin-roadmap.png"
            driver.save_screenshot(str(shot))
            ok("screenshot", str(shot))
        except Exception as e:
            fail("screenshot", e)

        # 7. No SEVERE JS errors (favicon/404 excluded)
        try:
            errors = []
            for entry in driver.get_log("browser"):
                if entry.get("level") != "SEVERE":
                    continue
                msg = entry.get("message", "")
                if any(tok in msg.lower() for tok in ("favicon", "404")):
                    continue
                errors.append(msg[:200])
            if errors:
                fail("no SEVERE JS errors", errors[0])
            else:
                ok("no SEVERE JS errors")
        except Exception as e:
            fail("no SEVERE JS errors", e)

    finally:
        driver.quit()

    summary = {
        "passed": len(passed),
        "failed": len(failed),
        "total": len(passed) + len(failed),
        "failures": failed,
    }
    print("\n" + "=" * 60)
    print(json.dumps(summary, indent=2))
    print("=" * 60)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
